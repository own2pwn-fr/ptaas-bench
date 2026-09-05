package com.calderwood.meridian.notifications;

import com.calderwood.meridian.security.Actor;
import com.calderwood.meridian.security.CurrentActor;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** Notification templates and what was sent from them. */
@RestController
@RequestMapping("/api/notifications")
public class NotificationController {

    private final JdbcTemplate jdbc;
    private final TemplateRenderer renderer;

    public NotificationController(JdbcTemplate jdbc, TemplateRenderer renderer) {
        this.jdbc = jdbc;
        this.renderer = renderer;
    }

    public record PreviewRequest(String template, Map<String, String> sample) {
    }

    @GetMapping("/templates")
    public Map<String, Object> templates() {
        CurrentActor.required();
        return Map.of("templates", jdbc.queryForList(
                "SELECT t.id, t.code, t.name, t.channel, t.body, t.updated_at,"
                        + " s.display_name AS updated_by"
                        + " FROM notification_templates t LEFT JOIN staff s ON s.id = t.updated_by"
                        + " ORDER BY t.code"));
    }

    /**
     * Fill a template with a sample consignment, so the desk can see it before saving.
     *
     * <p>The render runs on the pool rather than on this thread; the wait here is only
     * so the screen gets an answer in one request.
     */
    @PostMapping("/preview")
    public ResponseEntity<Map<String, Object>> preview(@RequestBody PreviewRequest request) {
        Actor caller = CurrentActor.required();
        if (request == null || request.template() == null) {
            return ResponseEntity.badRequest().body(Map.of("error", "No template was supplied."));
        }
        Map<String, String> values = new HashMap<>(TemplateRenderer.sampleValues());
        if (request.sample() != null) {
            values.putAll(request.sample());
        }
        String rendered;
        try {
            rendered = renderer.renderAsync(request.template(), values).get(20, TimeUnit.SECONDS);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            return ResponseEntity.status(503).body(Map.of("error", "The preview was interrupted."));
        } catch (ExecutionException | TimeoutException failed) {
            return ResponseEntity.status(422)
                    .body(Map.of("error", "That template could not be filled."));
        }
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("template", request.template());
        body.put("rendered", rendered);
        body.put("values", values);
        body.put("previewedBy", caller.displayName());
        return ResponseEntity.ok(body);
    }

    @GetMapping("/log")
    public Map<String, Object> log(@RequestParam(defaultValue = "0") int page,
                                   @RequestParam(defaultValue = "25") int size) {
        Actor caller = CurrentActor.required();
        int limit = Math.min(Math.max(size, 1), 100);
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT id, template_code, account_id, channel, recipient, sent_at, state, detail"
                        + " FROM notification_log WHERE (? IS NULL OR account_id = ?)"
                        + " ORDER BY sent_at DESC LIMIT ? OFFSET ?",
                caller.isAdministrator() ? null : caller.accountId(),
                caller.isAdministrator() ? null : caller.accountId(),
                limit, Math.max(page, 0) * limit);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("entries", rows);
        body.put("page", Math.max(page, 0));
        body.put("size", limit);
        return body;
    }
}
