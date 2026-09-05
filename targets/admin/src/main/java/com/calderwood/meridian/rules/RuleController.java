package com.calderwood.meridian.rules;

import com.calderwood.meridian.audit.AuditService;
import com.calderwood.meridian.platform.Anomalies;
import com.calderwood.meridian.security.Actor;
import com.calderwood.meridian.security.CurrentActor;
import jakarta.servlet.http.HttpServletRequest;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Routing rules.
 *
 * <p>Operations author these without a release, so a rule is an expression over a
 * consignment and the preview button is the only way to check one before saving it.
 */
@RestController
@RequestMapping("/api/rules")
public class RuleController {

    /** The row a rule reads. */
    public record Shipment(String reference, String originCode, String destinationCode,
                           String mode, double weightKg, double volumeM3, String accountTier,
                           boolean hazardous) {
    }

    private static final Shipment SAMPLE = new Shipment(
            "CW-40118", "GBFXT", "SEGOT", "rail", 1840.0, 12.5, "priority", false);

    private final JdbcTemplate jdbc;
    private final ExpressionEvaluator evaluator;
    private final AuditService audit;

    public RuleController(JdbcTemplate jdbc, ExpressionEvaluator evaluator, AuditService audit) {
        this.jdbc = jdbc;
        this.evaluator = evaluator;
        this.audit = audit;
    }

    public record PreviewRequest(String expression, Map<String, Object> sample) {
    }

    public record RuleRequest(String name, String expression, Integer priority, Boolean enabled) {
    }

    @GetMapping
    public Map<String, Object> list() {
        CurrentActor.required();
        return Map.of("rules", jdbc.queryForList(
                "SELECT r.id, r.name, r.expression, r.priority, r.enabled, r.updated_at,"
                        + " s.display_name AS updated_by"
                        + " FROM routing_rules r LEFT JOIN staff s ON s.id = r.updated_by"
                        + " ORDER BY r.priority, r.id"));
    }

    @GetMapping("/{id}")
    public ResponseEntity<Map<String, Object>> rule(@PathVariable long id) {
        CurrentActor.required();
        List<Map<String, Object>> found = jdbc.queryForList(
                "SELECT id, name, expression, priority, enabled, updated_at FROM routing_rules"
                        + " WHERE id = ?", id);
        return found.isEmpty() ? ResponseEntity.notFound().build() : ResponseEntity.ok(found.get(0));
    }

    /** Check a rule against a sample consignment before saving it. */
    @PostMapping("/preview")
    public ResponseEntity<Map<String, Object>> preview(@RequestBody PreviewRequest request) {
        CurrentActor.required();
        if (request == null || request.expression() == null || request.expression().isBlank()) {
            return ResponseEntity.badRequest().body(Map.of("error", "No expression was supplied."));
        }
        Shipment shipment = merge(request.sample());
        try {
            Object outcome = evaluator.evaluate(
                    Anomalies.RULE_HOST_TYPE_REACHED, request.expression(), shipment);
            Map<String, Object> body = new LinkedHashMap<>();
            body.put("expression", request.expression());
            body.put("shipment", shipment);
            body.put("matches", Boolean.TRUE.equals(outcome));
            body.put("result", outcome == null ? null : String.valueOf(outcome));
            return ResponseEntity.ok(body);
        } catch (RuntimeException unparseable) {
            return ResponseEntity.badRequest().body(Map.of(
                    "error", "That expression could not be applied to a consignment.",
                    "detail", String.valueOf(unparseable.getMessage())));
        }
    }

    @PostMapping
    public ResponseEntity<Map<String, Object>> create(@RequestBody RuleRequest request,
                                                      HttpServletRequest http) {
        Actor caller = CurrentActor.required();
        if (request == null || request.name() == null || request.expression() == null) {
            return ResponseEntity.badRequest().body(Map.of("error", "A rule needs a name and a clause."));
        }
        jdbc.update("INSERT INTO routing_rules"
                        + " (name, expression, priority, enabled, updated_by, updated_at)"
                        + " VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                request.name(), request.expression(),
                request.priority() == null ? 100 : request.priority(),
                request.enabled() == null || request.enabled() ? 1 : 0, caller.id());
        audit.record(caller.id(), "rule.created", "routing_rule", request.name(),
                caller.accountId(), http, request.expression());
        return ResponseEntity.status(201).body(Map.of("created", true, "name", request.name()));
    }

    private static Shipment merge(Map<String, Object> supplied) {
        if (supplied == null || supplied.isEmpty()) {
            return SAMPLE;
        }
        return new Shipment(
                text(supplied, "reference", SAMPLE.reference()),
                text(supplied, "originCode", SAMPLE.originCode()),
                text(supplied, "destinationCode", SAMPLE.destinationCode()),
                text(supplied, "mode", SAMPLE.mode()),
                number(supplied, "weightKg", SAMPLE.weightKg()),
                number(supplied, "volumeM3", SAMPLE.volumeM3()),
                text(supplied, "accountTier", SAMPLE.accountTier()),
                Boolean.TRUE.equals(supplied.get("hazardous")));
    }

    private static String text(Map<String, Object> map, String key, String fallback) {
        Object value = map.get(key);
        return value == null ? fallback : String.valueOf(value);
    }

    private static double number(Map<String, Object> map, String key, double fallback) {
        Object value = map.get(key);
        return value instanceof Number n ? n.doubleValue() : fallback;
    }
}
