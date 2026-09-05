package internal.telemetry.spring;

import internal.telemetry.RequestContext;
import internal.telemetry.TelemetryContext;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.web.servlet.HandlerInterceptor;
import org.springframework.web.servlet.HandlerMapping;

/**
 * Captures the route template at the moment the dispatcher resolves it.
 *
 * <p>The filter can read the same attribute after the chain returns, and normally does.
 * This interceptor exists for the cases where that is too late or too indirect:
 *
 * <ul>
 *   <li>an exception resolver or an error dispatch replaces the routing attributes with
 *       those of the error page, so a 500 would be filed under {@code /error} rather
 *       than under the endpoint that produced it;</li>
 *   <li>anything raised deep inside the handler — a counter, an outbound dependency
 *       link — wants the template <em>while the handler is running</em>, not afterwards,
 *       and reads it from the request context this interceptor fills in;</li>
 *   <li>a service that composes several dispatcher servlets resolves its prefix here,
 *       once, rather than reconstructing it later.</li>
 * </ul>
 *
 * <p>It changes nothing about how the request is handled: {@code preHandle} always
 * returns {@code true}, and every failure inside it is swallowed.
 */
public final class RouteTemplateInterceptor implements HandlerInterceptor {

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        try {
            RequestContext context = TelemetryContext.current();
            if (context == null) {
                return true;
            }
            Object pattern = request.getAttribute(HandlerMapping.BEST_MATCHING_PATTERN_ATTRIBUTE);
            if (pattern == null) {
                return true;
            }
            String template = String.valueOf(pattern);
            if (!template.isEmpty()) {
                context.route(prefixOf(request) + template);
            }
        } catch (RuntimeException | LinkageError unavailable) {
            // An interceptor that can fail a request is worse than a missing label.
        }
        return true;
    }

    /**
     * The path the dispatcher is mounted at, recovered by subtracting the path it
     * matched against from the path the request carried.
     */
    private static String prefixOf(HttpServletRequest request) {
        Object within = request.getAttribute(HandlerMapping.PATH_WITHIN_HANDLER_MAPPING_ATTRIBUTE);
        if (!(within instanceof String matched) || matched.isEmpty()) {
            return "";
        }
        String uri = request.getRequestURI();
        if (uri == null) {
            return "";
        }
        String context = request.getContextPath();
        String appPath = context != null && !context.isEmpty() && uri.startsWith(context)
                ? uri.substring(context.length())
                : uri;
        return appPath.endsWith(matched) && appPath.length() > matched.length()
                ? appPath.substring(0, appPath.length() - matched.length())
                : "";
    }
}
