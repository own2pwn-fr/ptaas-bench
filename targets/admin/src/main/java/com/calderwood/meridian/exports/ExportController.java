package com.calderwood.meridian.exports;

import com.calderwood.meridian.audit.AuditService;
import com.calderwood.meridian.security.Actor;
import com.calderwood.meridian.security.CurrentActor;
import jakarta.servlet.http.HttpServletRequest;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** Statement rendering and batch exports. */
@RestController
@RequestMapping("/api/exports")
public class ExportController {

    private final StatementRenderer renderer;
    private final BatchExporter batches;
    private final JdbcTemplate jdbc;
    private final AuditService audit;

    public ExportController(StatementRenderer renderer, BatchExporter batches, JdbcTemplate jdbc,
                            AuditService audit) {
        this.renderer = renderer;
        this.batches = batches;
        this.jdbc = jdbc;
        this.audit = audit;
    }

    public record RenderRequest(String statementId, String stylesheet) {
    }

    public record BatchRequest(String format, Long rows) {
    }

    /** The layouts the desk can pick from. */
    @GetMapping("/templates")
    public Map<String, Object> templates() {
        CurrentActor.required();
        return Map.of("layouts", List.of(renderer.stored()));
    }

    /**
     * Render one account statement.
     *
     * <p>{@code stylesheet} is either the name of a stored layout or a layout supplied
     * by the integration team for a client that has its own.
     */
    @PostMapping("/render")
    public ResponseEntity<Map<String, Object>> render(@RequestBody RenderRequest request,
                                                      HttpServletRequest http) {
        Actor caller = CurrentActor.required();
        if (request == null || request.statementId() == null) {
            return ResponseEntity.badRequest().body(Map.of("error", "No statement was named."));
        }
        if (!renderer.hasStatement(request.statementId())) {
            return ResponseEntity.status(404).body(Map.of("error", "No such statement."));
        }
        try {
            String rendered = renderer.render(request.statementId(), request.stylesheet());
            audit.record(caller.id(), "export.render", "statement", request.statementId(),
                    caller.accountId(), http, "layout supplied by the caller");
            Map<String, Object> body = new LinkedHashMap<>();
            body.put("statementId", request.statementId());
            body.put("length", rendered.length());
            body.put("document", rendered);
            return ResponseEntity.ok(body);
        } catch (IllegalArgumentException unknown) {
            return ResponseEntity.badRequest().body(Map.of("error", unknown.getMessage()));
        } catch (Exception failed) {
            return ResponseEntity.status(422)
                    .body(Map.of("error", "The layout could not be applied to that statement."));
        }
    }

    /** Queue a batch export of the current grid. */
    @PostMapping("/batch")
    public ResponseEntity<Map<String, Object>> batch(@RequestBody BatchRequest request,
                                                     HttpServletRequest http) {
        Actor caller = CurrentActor.required();
        String format = request == null || request.format() == null ? "csv" : request.format();
        long rows = request == null || request.rows() == null ? 500L : request.rows();
        try {
            long reserved = batches.reserve(rows, format);
            jdbc.update("INSERT INTO export_jobs"
                            + " (requested_by, format, row_count, state, created_at)"
                            + " VALUES (?, ?, ?, 'queued', CURRENT_TIMESTAMP)",
                    caller.id(), format, (int) Math.min(rows, Integer.MAX_VALUE));
            audit.record(caller.id(), "export.batch", "export", format, caller.accountId(), http,
                    rows + " rows");
            Map<String, Object> body = new LinkedHashMap<>();
            body.put("queued", true);
            body.put("format", format);
            body.put("rows", rows);
            body.put("reservedBytes", reserved);
            return ResponseEntity.accepted().body(body);
        } catch (IllegalStateException tooLarge) {
            return ResponseEntity.status(507).body(Map.of("error", tooLarge.getMessage()));
        }
    }

    /** Exports run recently. */
    @GetMapping("/history")
    public Map<String, Object> history(@RequestParam(defaultValue = "0") int page,
                                       @RequestParam(defaultValue = "25") int size) {
        Actor caller = CurrentActor.required();
        int limit = Math.min(Math.max(size, 1), 100);
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT j.id, j.format, j.row_count, j.state, j.created_at, j.completed_at,"
                        + " s.display_name AS requested_by"
                        + " FROM export_jobs j LEFT JOIN staff s ON s.id = j.requested_by"
                        + " ORDER BY j.created_at DESC LIMIT ? OFFSET ?",
                limit, Math.max(page, 0) * limit);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("jobs", rows);
        body.put("page", Math.max(page, 0));
        body.put("size", limit);
        body.put("requestedBy", caller.displayName());
        return body;
    }
}
