package internal.telemetry.servlet;

import jakarta.servlet.http.HttpServletRequest;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * The route template a request matched, as the framework registered it.
 *
 * <p>Records are grouped by template and never by URL. {@code /api/orders/{id}} is one
 * time series; {@code /api/orders/40118} and its ten thousand siblings are ten thousand
 * useless ones, and a dashboard built on the second shape cannot answer any question at
 * all. The concrete path travels alongside on the same record, so nothing is lost.
 *
 * <p>The template is read from the request attributes the dispatcher sets while routing.
 * They are addressed by name rather than through the framework's constants so that a
 * service which is not built on that framework still links against this library; the
 * names are exactly the values of
 * {@code HandlerMapping.BEST_MATCHING_PATTERN_ATTRIBUTE},
 * {@code HandlerMapping.PATH_WITHIN_HANDLER_MAPPING_ATTRIBUTE} and
 * {@code HandlerMapping.URI_TEMPLATE_VARIABLES_ATTRIBUTE}. A unit test compares the
 * three literals below against those constants, so a rename upstream fails a build here
 * rather than quietly emptying a dashboard.
 *
 * <p>Reading them after the chain returns is safe: request attributes outlive the
 * dispatch, unlike the router-local state some frameworks unwind on the way out.
 */
public final class RouteTemplate {

    /** Reported when nothing matched: a 404, a static asset, a request the router refused. */
    public static final String UNMATCHED = "<unmatched>";

    public static final String BEST_MATCHING_PATTERN =
            "org.springframework.web.servlet.HandlerMapping.bestMatchingPattern";
    public static final String PATH_WITHIN_HANDLER_MAPPING =
            "org.springframework.web.servlet.HandlerMapping.pathWithinHandlerMapping";
    public static final String URI_TEMPLATE_VARIABLES =
            "org.springframework.web.servlet.HandlerMapping.uriTemplateVariables";

    private RouteTemplate() {
    }

    /**
     * The template, or {@link #UNMATCHED}.
     *
     * <p>The dispatcher's pattern is relative to wherever the dispatcher itself is
     * mapped, so a service mounted under a prefix would otherwise report every one of
     * its endpoints under the bare pattern and collapse two different services onto one
     * name. The prefix is recovered by subtracting the path the dispatcher matched
     * against from the path the request actually carried.
     */
    public static String of(HttpServletRequest request) {
        Object pattern = request.getAttribute(BEST_MATCHING_PATTERN);
        if (pattern == null) {
            return UNMATCHED;
        }
        String template = String.valueOf(pattern);
        if (template.isEmpty()) {
            return UNMATCHED;
        }
        return prefixOf(request) + template;
    }

    private static String prefixOf(HttpServletRequest request) {
        Object within = request.getAttribute(PATH_WITHIN_HANDLER_MAPPING);
        if (!(within instanceof String matched) || matched.isEmpty()) {
            return "";
        }
        String uri = request.getRequestURI();
        String context = request.getContextPath();
        if (uri == null) {
            return "";
        }
        String appPath = context != null && !context.isEmpty() && uri.startsWith(context)
                ? uri.substring(context.length())
                : uri;
        if (appPath.endsWith(matched) && appPath.length() > matched.length()) {
            return appPath.substring(0, appPath.length() - matched.length());
        }
        return "";
    }

    /** Path variables the router bound, e.g. {@code {orgId} -> 1042}. */
    public static Map<String, String> pathVariables(HttpServletRequest request) {
        Map<String, String> out = new LinkedHashMap<>();
        Object raw = request.getAttribute(URI_TEMPLATE_VARIABLES);
        if (raw instanceof Map<?, ?> variables) {
            for (Map.Entry<?, ?> entry : variables.entrySet()) {
                if (entry.getKey() != null) {
                    out.put(String.valueOf(entry.getKey()), String.valueOf(entry.getValue()));
                }
            }
        }
        return out;
    }
}
