package internal.telemetry.servlet;

import internal.telemetry.Attribute;
import internal.telemetry.AttributeCollector;
import internal.telemetry.Attributes;
import internal.telemetry.RequestContext;
import internal.telemetry.Telemetry;
import internal.telemetry.TelemetryClient;
import internal.telemetry.TelemetryContext;
import jakarta.servlet.AsyncEvent;
import jakarta.servlet.AsyncListener;
import jakarta.servlet.Filter;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.ServletRequest;
import jakarta.servlet.ServletResponse;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.servlet.http.Part;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Collection;
import java.util.Enumeration;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.function.Function;

/**
 * Exports exactly one request record per served request.
 *
 * <p>Install it first, ahead of everything else:
 * <pre>{@code
 * @Bean
 * FilterRegistrationBean<TelemetryFilter> telemetryFilter(TelemetryClient telemetry) {
 *     var registration = new FilterRegistrationBean<>(new TelemetryFilter(telemetry));
 *     registration.setOrder(Ordered.HIGHEST_PRECEDENCE);
 *     return registration;
 * }
 * }</pre>
 *
 * <p>Ordering matters for one reason: the address of the peer has to be read before any
 * other filter has had the chance to rewrite it. {@link PeerAddress} does not depend on
 * that ordering being respected, but it is still the right place to be.
 *
 * <h2>What it does not do</h2>
 *
 * <p>It sets no response header, adds no route, writes no log line on the happy path,
 * touches no error body and changes no timing that a client could measure. A client, a
 * cache, or a capture taken of this service looks the same whether the library is
 * loaded or not. That is not tidiness: an agent that changes what a caller observes
 * changes what the service's own numbers mean.
 *
 * <h2>Exactly once</h2>
 *
 * <p>A single HTTP request can enter a filter chain several times — the initial
 * dispatch, an asynchronous re-dispatch, an error dispatch, an internal forward. A
 * marker on the request keeps that to one record. Asynchronous requests are recorded
 * from a completion listener rather than when the chain returns, because when the chain
 * returns on an asynchronous request the response has not happened yet.
 */
public class TelemetryFilter implements Filter {

    private static final String CONTEXT_ATTRIBUTE = "internal.telemetry.context";
    private static final String RECORDED_ATTRIBUTE = "internal.telemetry.recorded";

    private final TelemetryClient telemetry;
    private final Function<HttpServletRequest, String> identify;

    public TelemetryFilter() {
        this(null, null);
    }

    public TelemetryFilter(TelemetryClient telemetry) {
        this(telemetry, null);
    }

    /**
     * @param telemetry the client to report to; the process-wide one when {@code null}
     * @param identify  resolves the authenticated principal from the request. The
     *                  default reads whatever the application declared through
     *                  {@link TelemetryClient#authSubject(String)}, which is the only
     *                  thing a library can know about a host's notion of identity.
     */
    public TelemetryFilter(TelemetryClient telemetry, Function<HttpServletRequest, String> identify) {
        this.telemetry = telemetry;
        this.identify = identify;
    }

    private TelemetryClient client() {
        return telemetry != null ? telemetry : Telemetry.get();
    }

    @Override
    public void doFilter(ServletRequest rawRequest, ServletResponse rawResponse, FilterChain chain)
            throws IOException, ServletException {

        if (!(rawRequest instanceof HttpServletRequest request)
                || !(rawResponse instanceof HttpServletResponse response)) {
            chain.doFilter(rawRequest, rawResponse);
            return;
        }

        TelemetryClient active;
        try {
            active = client();
        } catch (RuntimeException unconfigured) {
            chain.doFilter(rawRequest, rawResponse);
            return;
        }
        if (!active.enabled() || request.getAttribute(CONTEXT_ATTRIBUTE) != null) {
            // Already instrumented on an earlier dispatch of this same request, or the
            // library is inert. Either way the chain runs untouched.
            chain.doFilter(rawRequest, rawResponse);
            return;
        }

        RequestContext context;
        CachingRequest cached;
        try {
            String peerIp = PeerAddress.of(request);
            context = new RequestContext(
                    UUID.randomUUID().toString(),
                    peerIp,
                    PeerAddress.clientIp(request),
                    active.isSyntheticPeer(peerIp));
            request.setAttribute(CONTEXT_ATTRIBUTE, context);
            cached = new CachingRequest(request, active.config().maxBodyBytes());
        } catch (RuntimeException failed) {
            // Falling through to the chain is the only acceptable failure mode.
            chain.doFilter(rawRequest, rawResponse);
            return;
        }

        boolean asynchronous = false;
        try (TelemetryContext.Scope ignored = TelemetryContext.open(context)) {
            chain.doFilter(cached, response);
            asynchronous = request.isAsyncStarted();
            if (asynchronous) {
                registerCompletion(request, active, context, cached, response);
            }
        } finally {
            if (!asynchronous) {
                record(active, request, cached, response, context);
            }
        }
    }

    private void registerCompletion(HttpServletRequest request, TelemetryClient active,
                                    RequestContext context, CachingRequest cached,
                                    HttpServletResponse response) {
        try {
            request.getAsyncContext().addListener(new AsyncListener() {
                @Override
                public void onComplete(AsyncEvent event) {
                    record(active, request, cached, response, context);
                }

                @Override
                public void onTimeout(AsyncEvent event) {
                    // onComplete still follows a timeout; nothing to do here, and
                    // recording twice would be worse than recording late.
                }

                @Override
                public void onError(AsyncEvent event) {
                    // As above.
                }

                @Override
                public void onStartAsync(AsyncEvent event) {
                    // A re-dispatch that starts async again keeps the same listener.
                }
            });
        } catch (RuntimeException noAsyncContext) {
            record(active, request, cached, response, context);
        }
    }

    // ------------------------------------------------------------------ recording

    private void record(TelemetryClient active, HttpServletRequest request, CachingRequest cached,
                        HttpServletResponse response, RequestContext context) {
        if (request.getAttribute(RECORDED_ATTRIBUTE) != null) {
            return;
        }
        request.setAttribute(RECORDED_ATTRIBUTE, Boolean.TRUE);
        try {
            AttributeCollector collector = active.newCollector();
            collectAll(collector, request, cached);
            collector.addAll(context.extraAttributes());

            String route = context.route() != null ? context.route() : RouteTemplate.of(request);
            context.route(route);

            active.recordRequest(
                    method(request),
                    route,
                    request.getRequestURI(),
                    response.getStatus(),
                    collector.entries(),
                    subjectOf(request, context),
                    context.clientIp(),
                    header(request, "user-agent"),
                    context.requestId(),
                    context.synthetic(),
                    context.peerIp());
        } catch (RuntimeException | LinkageError never) {
            // Never propagate, never log. A stack trace on standard output would put
            // this library's noise into the application's own logs, where it does not
            // belong and where somebody would eventually have to explain it.
        }
    }

    private String subjectOf(HttpServletRequest request, RequestContext context) {
        if (context.authSubject() != null) {
            return context.authSubject();
        }
        if (identify != null) {
            try {
                return identify.apply(request);
            } catch (RuntimeException unresolved) {
                return null;
            }
        }
        java.security.Principal principal = request.getUserPrincipal();
        return principal == null ? null : principal.getName();
    }

    /**
     * Describe every input the handler could have observed.
     *
     * <p>Runs after the response has been produced, so none of this work lands in the
     * endpoint's latency. The order below is the order a reader wants to see them in,
     * and de-duplication inside the collector is on the value as well as the name, so a
     * parameter that appears twice with two different values appears twice here.
     */
    private void collectAll(AttributeCollector collector, HttpServletRequest request,
                            CachingRequest cached) {
        collector.addPairs(request.getQueryString(), "query");

        for (Map.Entry<String, String> variable : RouteTemplate.pathVariables(request).entrySet()) {
            collector.add(variable.getKey(), "path", variable.getValue());
        }

        String contentType = request.getContentType();
        byte[] body = cached.captured();
        if (body.length > 0) {
            collector.addBody(body, contentType);
        } else {
            // Nothing came through the stream. Either the container parsed the body
            // itself on the handler's behalf — which is what happens when the handler
            // asked for form fields or parts instead of for bytes — or nobody read it
            // at all, which is the case for a route that refused the request before
            // looking. Try the parsed view first, then take the bytes ourselves.
            int before = collector.size();
            recoverParsedBody(collector, request, contentType);
            if (collector.size() == before) {
                cached.drainRemaining();
                body = cached.captured();
                if (body.length > 0) {
                    collector.addBody(body, contentType);
                }
            }
        }

        collector.addCookieHeader(header(request, "cookie"));

        Enumeration<String> names = request.getHeaderNames();
        if (names != null) {
            while (names.hasMoreElements()) {
                String name = names.nextElement();
                if (name == null) {
                    continue;
                }
                String lowered = name.toLowerCase(java.util.Locale.ROOT);
                if (lowered.equals("cookie") || !Attributes.isDescribedHeader(lowered)) {
                    continue;
                }
                Enumeration<String> values = request.getHeaders(name);
                while (values != null && values.hasMoreElements()) {
                    collector.add(lowered, "header", values.nextElement());
                }
            }
        }
    }

    /**
     * Recover a body the container parsed on the application's behalf.
     *
     * <p>Form fields come back from the parameter map with the query string mixed in,
     * so query pairs are subtracted by name and value: adding them again under
     * {@code body} would claim the client sent a field it did not.
     */
    private void recoverParsedBody(AttributeCollector collector, HttpServletRequest request,
                                   String contentType) {
        String base = Attributes.baseContentType(contentType);
        try {
            if (base.equals("application/x-www-form-urlencoded")) {
                java.util.Set<String> fromQuery = new java.util.HashSet<>();
                for (String[] pair : Attributes.parsePairs(request.getQueryString())) {
                    fromQuery.add(pair[0] + "=" + pair[1]);
                }
                for (Map.Entry<String, String[]> entry : request.getParameterMap().entrySet()) {
                    for (String value : entry.getValue()) {
                        if (fromQuery.contains(entry.getKey() + "=" + value)) {
                            continue;
                        }
                        collector.add(entry.getKey(), "body", value);
                    }
                }
                return;
            }
            if (base.startsWith("multipart/")) {
                Collection<Part> parts = request.getParts();
                for (Part part : parts) {
                    String name = part.getName();
                    String filename = part.getSubmittedFileName();
                    if (filename != null) {
                        collector.add(name + ".filename", "multipart", filename);
                        collector.add(name, "multipart", new byte[0]);
                        continue;
                    }
                    // A small field is worth describing by value; a large one is not a
                    // field, it is an upload that arrived without a file name.
                    if (part.getSize() >= 0 && part.getSize() <= 8192) {
                        try (java.io.InputStream in = part.getInputStream()) {
                            collector.add(name, "multipart", in.readAllBytes());
                        }
                    } else {
                        collector.add(name, "multipart", new byte[0]);
                    }
                }
            }
        } catch (IOException | ServletException | RuntimeException unavailable) {
            // No multipart configuration, a consumed stream, a container that refuses
            // to re-parse: one missing description, never a failed request.
        }
    }

    private static String method(HttpServletRequest request) {
        String method = request.getMethod();
        return method == null ? "GET" : method.toUpperCase(java.util.Locale.ROOT);
    }

    private static String header(HttpServletRequest request, String name) {
        String value = request.getHeader(name);
        return value == null ? "" : value;
    }

    /**
     * Describe inputs a handler saw that no filter could: a document embedded in a
     * body, a frame lifted off a socket, a message pulled from a queue mid-request.
     */
    public static void contribute(List<Attribute> attributes) {
        Telemetry.get().contribute(attributes, RouteTemplate.UNMATCHED, "GET");
    }

    /** UTF-8, spelled out once so the charset is never the platform default. */
    static byte[] utf8(String value) {
        return value.getBytes(StandardCharsets.UTF_8);
    }
}
