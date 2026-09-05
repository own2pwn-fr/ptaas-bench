package internal.telemetry;

import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.regex.Pattern;

/**
 * The handle an application talks to.
 *
 * <p>Every public method is total: it swallows its own errors and returns without
 * complaint. Instrumentation that can fail a request is worse than no instrumentation,
 * so the worst outcome any call here can produce is a missing data point.
 *
 * <p>Typical wiring, once, at start-up:
 * <pre>{@code
 * TelemetryClient telemetry = Telemetry.init();      // TELEMETRY_SERVICE / TELEMETRY_ENDPOINT
 * }</pre>
 * and then, at the point of interest, anywhere in the call stack:
 * <pre>{@code
 * telemetry.signal("console.reporting.ledger.statement_stall",
 *                  SignalOptions.payload(window).withDetail("statement held " + millis + "ms"));
 * }</pre>
 */
public final class TelemetryClient {

    /**
     * Signal names are metric names: dotted, lower case, at least three segments.
     *
     * <p>This expression is the collector's, character for character. That is not
     * tidiness. A name the collector refuses is dropped there with no error anywhere —
     * the caller gets its 202 like everything else — so the series simply never appears
     * and the only visible symptom is a counter that stayed at zero. Validating with
     * the identical rule here turns that into a number in {@link #stats()}.
     *
     * <p>Applied on every path that carries a name, including the dependency-link path.
     * A link is the only record some outbound effects ever produce, so a name rejected
     * silently there costs the whole observation rather than one field of it.
     */
    public static final Pattern SIGNAL_NAME = Pattern.compile("^[a-z][a-z0-9]*(\\.[a-z0-9_]+){2,}$");

    /** The collector clips longer strings; clipping here keeps the queue honest too. */
    private static final int ATTRIBUTE_MAX_CHARS = 1024;

    private final TelemetryConfig config;
    private final Transport transport;
    private final java.util.concurrent.atomic.AtomicLong invalidNames =
            new java.util.concurrent.atomic.AtomicLong();

    public TelemetryClient() {
        this(TelemetryConfig.fromEnvironment());
    }

    public TelemetryClient(TelemetryConfig config) {
        this.config = config;
        this.transport = new Transport(config);
    }

    public TelemetryConfig config() {
        return config;
    }

    public boolean enabled() {
        return config.enabled();
    }

    public String service() {
        return config.service();
    }

    // ------------------------------------------------------------------ recording

    /**
     * Queue an already-built record, stamping service, timestamp and the facts of the
     * request in flight.
     *
     * <p>Explicit values always win: the filter has read the socket directly and is the
     * only place that can.
     */
    public void emit(Map<String, Object> record) {
        if (!config.enabled()) {
            return;
        }
        try {
            record.putIfAbsent("app", config.service());
            record.putIfAbsent("ts", System.currentTimeMillis() / 1000.0d);
            RequestContext context = TelemetryContext.current();
            if (context != null) {
                if (!record.containsKey("peer_ip") && !context.peerIp().isEmpty()) {
                    record.put("peer_ip", context.peerIp());
                }
                if (!record.containsKey("synthetic") && context.synthetic()) {
                    record.put("synthetic", Boolean.TRUE);
                }
            }
            transport.enqueue(record);
        } catch (RuntimeException never) {
            // Unreachable in practice; kept because "never raises" is a property this
            // class is relied on for, not an aspiration.
        }
    }

    /**
     * Record a named application signal.
     *
     * <p>Raise one where an anomalous <em>effect</em> is confirmed, never where a
     * suspicious input arrives. A counter that also counts inputs which turned out to be
     * inert is dominated by noise and stops being usable as an alert; worse, it makes
     * every later reader of the dashboard believe something happened that did not.
     */
    public void signal(String name) {
        signal(name, SignalOptions.none());
    }

    public void signal(String name, Map<String, Object> attributes) {
        SignalOptions options = SignalOptions.none();
        if (attributes != null) {
            attributes.forEach(options::with);
        }
        signal(name, options);
    }

    public void signal(String name, SignalOptions options) {
        if (!config.enabled()) {
            return;
        }
        try {
            if (name == null || !SIGNAL_NAME.matcher(name).matches()) {
                invalidNames.incrementAndGet();
                return;
            }
            RequestContext context = TelemetryContext.current();
            Map<String, Object> record = new LinkedHashMap<>();
            record.put("type", "signal");
            record.put("app", config.service());
            record.put("ts", System.currentTimeMillis() / 1000.0d);
            record.put("signal", name);

            Map<String, Object> attributes = new LinkedHashMap<>();
            for (Map.Entry<String, Object> entry : options.attributes().entrySet()) {
                if (entry.getValue() != null) {
                    attributes.put(entry.getKey(), clip(entry.getValue()));
                }
            }
            // An explicit request id wins: only the caller knows when a signal belongs
            // to a request other than the one in flight.
            if (!attributes.containsKey("request_id") && context != null) {
                attributes.put("request_id", context.requestId());
            }
            if (!attributes.isEmpty()) {
                record.put("attributes", attributes);
            }
            if (options.synthetic() != null) {
                record.put("synthetic", options.synthetic());
            }
            emit(record);
        } catch (RuntimeException never) {
            // As above.
        }
    }

    /** Free-form breadcrumb, kept beside the records of the same period. */
    public void note(String message) {
        if (!config.enabled()) {
            return;
        }
        try {
            Map<String, Object> record = new LinkedHashMap<>();
            record.put("type", "note");
            record.put("app", config.service());
            record.put("ts", System.currentTimeMillis() / 1000.0d);
            record.put("message", clip(message));
            emit(record);
        } catch (RuntimeException never) {
            // As above.
        }
    }

    /**
     * Register an outbound dependency call whose destination came from a request.
     *
     * <p>Call it immediately <em>before</em> the fetch:
     * <pre>{@code
     * telemetry.outbound(url, EgressDeclaration.from("console.integrations.probe.offlist_host_fetched")
     *                             .withParam("endpoint"));
     * http.send(...);
     * }</pre>
     *
     * <p>Dispatch is immediate and on its own connection rather than through the record
     * queue: the name lookup follows within microseconds, and anything that waited for
     * the next export tick would arrive after the effect it explains. Immediate still
     * means off the request path — this hands the record to another thread and returns.
     *
     * @return the correlation id, so the caller can attach it to later records
     */
    public String outbound(String destination, EgressDeclaration declaration) {
        EgressDeclaration options = declaration == null ? new EgressDeclaration() : declaration;
        String requestId = options.requestId();
        RequestContext context = TelemetryContext.current();
        if (requestId == null) {
            requestId = context != null ? context.requestId() : UUID.randomUUID().toString();
        }
        if (!config.enabled()) {
            return requestId;
        }
        try {
            Map<String, Object> link = new LinkedHashMap<>();
            link.put("app", config.service());
            link.put("ts", System.currentTimeMillis() / 1000.0d);
            link.put("destination_host", EgressDeclaration.hostOf(destination));
            link.put("request_id", requestId);
            if (options.signal() == null || !SIGNAL_NAME.matcher(options.signal()).matches()) {
                // Without a name the far end cannot say which code path made the call,
                // and a nameless link is indistinguishable from no link at all. Counted
                // and dropped, so the mistake is a number rather than a silence.
                invalidNames.incrementAndGet();
                return requestId;
            }
            link.put("signal", options.signal());
            if (options.param() != null) {
                link.put("param", options.param());
            }
            String route = options.route() != null ? options.route()
                    : (context != null ? context.route() : null);
            if (route != null) {
                link.put("route", route);
            }
            String peer = options.peerIp() != null ? options.peerIp()
                    : (context != null ? context.peerIp() : null);
            if (peer != null && !peer.isEmpty()) {
                link.put("peer_ip", peer);
            }
            if (context != null && !context.clientIp().isEmpty()) {
                link.put("client_ip", context.clientIp());
            }
            boolean synthetic = options.synthetic() != null
                    ? options.synthetic()
                    : (context != null && context.synthetic());
            link.put("synthetic", synthetic);
            transport.enqueueLink(link);
        } catch (RuntimeException never) {
            // As above.
        }
        return requestId;
    }

    /** Convenience overload for the common shape. */
    public String outbound(String destination, String signal, String param) {
        return outbound(destination, EgressDeclaration.from(signal).withParam(param));
    }

    // ------------------------------------------------------- request-shaped records

    /** Export one request record. Used by the filter and by the helpers below. */
    public void recordRequest(String method, String route, String path, Integer status,
                              List<Attribute> params, String authSubject, String clientIp,
                              String userAgent, String requestId, boolean synthetic, String peerIp) {
        if (!config.enabled()) {
            return;
        }
        try {
            Map<String, Object> record = new LinkedHashMap<>();
            record.put("type", "http_request");
            record.put("app", config.service());
            record.put("ts", System.currentTimeMillis() / 1000.0d);
            record.put("method", method);
            record.put("route", route);
            record.put("path", path);
            record.put("status", status);
            record.put("auth_subject", authSubject);
            record.put("client_ip", clientIp);
            record.put("user_agent", userAgent);
            record.put("synthetic", synthetic);
            if (peerIp != null && !peerIp.isEmpty()) {
                record.put("peer_ip", peerIp);
            }
            if (requestId != null) {
                record.put("request_id", requestId);
            }
            List<Map<String, Object>> described = new ArrayList<>(params.size());
            for (Attribute attribute : params) {
                described.add(attribute.toMap());
            }
            record.put("params", described);
            // Bypass emit()'s context stamping: the filter has already resolved both
            // fields from the socket, and the record is complete.
            transport.enqueue(record);
        } catch (RuntimeException never) {
            // As above.
        }
    }

    /**
     * Contribute described inputs to the record of the request in flight.
     *
     * <p>For a layer that sees inputs the filter cannot — a GraphQL document, a
     * WebSocket frame, a message pulled off a queue inside the request. Outside a
     * request the inputs are exported as a record of their own.
     */
    public void contribute(List<Attribute> attributes, String route, String method) {
        if (!config.enabled() || attributes == null || attributes.isEmpty()) {
            return;
        }
        RequestContext context = TelemetryContext.current();
        if (context != null) {
            context.extraAttributes().addAll(attributes);
            return;
        }
        recordRequest(method, route, route, null, attributes, null, "", "", null, false, null);
    }

    /** Declare the authenticated principal of the request in flight. */
    public void authSubject(String subject) {
        RequestContext context = TelemetryContext.current();
        if (context != null) {
            context.authSubject(subject);
        }
    }

    public String currentRequestId() {
        RequestContext context = TelemetryContext.current();
        return context == null ? null : context.requestId();
    }

    /** True when the socket peer sits in one of the configured generated-traffic networks. */
    public boolean isSyntheticPeer(String peerIp) {
        return config.syntheticSources().matches(peerIp);
    }

    public AttributeCollector newCollector() {
        return new AttributeCollector(config.maxParams());
    }

    // ------------------------------------------------------------------ lifecycle

    /** Test seam: route batches to {@code sender} instead of onto a socket. */
    public void sender(BatchSender sender) {
        transport.sender(sender);
    }

    public boolean flush(Duration budget) {
        return transport.flush(budget);
    }

    public void close(Duration budget) {
        transport.shutdown(budget);
    }

    public Map<String, Long> stats() {
        Map<String, Long> out = new LinkedHashMap<>(transport.stats());
        out.put("invalid_signals", invalidNames.get());
        return out;
    }

    private static String clip(Object value) {
        String text = value instanceof String s ? s : String.valueOf(value);
        return text.length() > ATTRIBUTE_MAX_CHARS ? text.substring(0, ATTRIBUTE_MAX_CHARS) : text;
    }
}
