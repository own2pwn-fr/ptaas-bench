package com.calderwood.meridian.support;

import com.calderwood.meridian.platform.Anomalies;
import internal.telemetry.SignalOptions;
import internal.telemetry.Telemetry;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * What the browser tells us about itself.
 *
 * <p>Two things arrive here: page views from the first-party metrics snippet, and render
 * diagnostics from the shell. The second exists because the notice banner is the only
 * place the console compiles a template at runtime, and a banner that renders to
 * something unintended is invisible from the server — the operator sees it, we do not.
 * So the shell reports what it painted and the two are compared here.
 */
@RestController
@RequestMapping("/api/client")
public class ClientTelemetryController {

    /** An interpolation as an operator writes one. */
    private static final Pattern INTERPOLATION = Pattern.compile("\\{\\{\\s*(.+?)\\s*}}", Pattern.DOTALL);

    private final JdbcTemplate jdbc;

    public ClientTelemetryController(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public record Diagnostic(String component, Long noticeId, String source, String painted,
                             String message, String stack) {
    }

    public record PageView(String path, String referrer, Integer viewportWidth, Long durationMs) {
    }

    @PostMapping("/metrics")
    public Map<String, Object> pageView(@RequestBody(required = false) PageView view) {
        // Counted in the aggregate the analytics snippet feeds; nothing per-visitor is
        // kept and nothing is written per request.
        return Map.of("accepted", true);
    }

    @PostMapping("/diagnostics")
    public Map<String, Object> diagnostic(@RequestBody(required = false) Diagnostic report) {
        if (report == null) {
            return Map.of("accepted", true);
        }
        if ("notice-banner".equals(report.component()) && report.noticeId() != null) {
            compare(report);
        }
        return Map.of("accepted", true);
    }

    /**
     * Compare a stored notice with what the shell painted from it.
     *
     * <p>A banner is supposed to paint the placeholders the operator wrote, filled in
     * from the figures the shell exposes. When the painted text no longer contains the
     * placeholder and carries something the stored body never did, the browser evaluated
     * more than the shell offers it — which is worth knowing about the notice that
     * caused it. Comparing against the stored body, rather than against whatever the
     * report claims the body was, is the point: the report is a browser talking.
     */
    private void compare(Diagnostic report) {
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT body FROM notices WHERE id = ?", report.noticeId());
        if (rows.isEmpty()) {
            return;
        }
        String stored = String.valueOf(rows.get(0).get("body"));
        String painted = report.painted() == null ? "" : report.painted();
        if (stored.isBlank() || painted.isBlank()) {
            return;
        }
        Matcher matcher = INTERPOLATION.matcher(stored);
        while (matcher.find()) {
            String literal = matcher.group();
            String inside = matcher.group(1);
            boolean placeholderGone = !painted.contains(literal);
            boolean paintedSomethingElse = !painted.contains(inside) && !painted.equals(stored);
            if (placeholderGone && paintedSomethingElse) {
                Telemetry.signal(Anomalies.NOTICE_EXPRESSION_EVALUATED,
                        SignalOptions.payload(literal)
                                .withDetail("notice " + report.noticeId() + " painted as "
                                        + clip(painted) + "; the stored body still reads "
                                        + clip(stored)));
                return;
            }
        }
    }

    private static String clip(String value) {
        String flat = value.replaceAll("\\s+", " ").trim();
        return flat.length() <= 160 ? flat : flat.substring(0, 160);
    }
}
