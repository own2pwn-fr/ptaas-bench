package com.calderwood.meridian.approvals;

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
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * The approvals queue.
 *
 * <p>Credit limits, tariff exceptions and write-offs above the desk's authority land
 * here. Anyone signed in can see the queue; recording a decision is an administrator's
 * job, and the path rules say so.
 */
@RestController
@RequestMapping("/api/approvals")
public class ApprovalController {

    private final JdbcTemplate jdbc;
    private final AuditService audit;
    private final ApprovalService approvals;

    public ApprovalController(JdbcTemplate jdbc, AuditService audit, ApprovalService approvals) {
        this.jdbc = jdbc;
        this.audit = audit;
        this.approvals = approvals;
    }

    public record Decision(String decision, String note) {
    }

    @GetMapping
    public Map<String, Object> queue(@RequestParam(defaultValue = "pending") String state,
                                     @RequestParam(defaultValue = "0") int page,
                                     @RequestParam(defaultValue = "25") int size) {
        Actor caller = CurrentActor.required();
        int limit = Math.min(Math.max(size, 1), 100);
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT a.id, a.account_id, a.subject_type, a.subject_reference, a.requested_at,"
                        + " a.state, a.decided_at, a.note, r.display_name AS requested_by,"
                        + " d.display_name AS decided_by, c.name AS account_name"
                        + " FROM approvals a"
                        + " LEFT JOIN staff r ON r.id = a.requested_by"
                        + " LEFT JOIN staff d ON d.id = a.decided_by"
                        + " LEFT JOIN accounts c ON c.id = a.account_id"
                        + " WHERE (? = 'all' OR a.state = ?)"
                        + " AND (? IS NULL OR a.account_id = ?)"
                        + " ORDER BY a.requested_at DESC LIMIT ? OFFSET ?",
                state, state,
                caller.isAdministrator() ? null : caller.accountId(),
                caller.isAdministrator() ? null : caller.accountId(),
                limit, Math.max(page, 0) * limit);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("approvals", rows);
        body.put("state", state);
        body.put("page", Math.max(page, 0));
        body.put("size", limit);
        return body;
    }

    @GetMapping("/{id}")
    public ResponseEntity<Map<String, Object>> approval(@PathVariable long id) {
        CurrentActor.required();
        List<Map<String, Object>> found = jdbc.queryForList(
                "SELECT a.*, r.display_name AS requested_by_name, c.name AS account_name"
                        + " FROM approvals a"
                        + " LEFT JOIN staff r ON r.id = a.requested_by"
                        + " LEFT JOIN accounts c ON c.id = a.account_id WHERE a.id = ?", id);
        return found.isEmpty() ? ResponseEntity.notFound().build() : ResponseEntity.ok(found.get(0));
    }

    /**
     * Record a decision.
     *
     * <p>Split out of the approval resource when the decision grew a note and an
     * outcome; the screen posts here from the detail drawer.
     */
    @PostMapping("/{id}/decision")
    public ResponseEntity<Map<String, Object>> decide(@PathVariable long id,
                                                      @RequestBody Decision body,
                                                      HttpServletRequest http) {
        Actor caller = CurrentActor.required();
        String outcome = body == null || body.decision() == null ? "" : body.decision().trim();
        if (!"approve".equals(outcome) && !"reject".equals(outcome)) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "A decision is either approve or reject."));
        }
        ApprovalService.Outcome recorded =
                approvals.decide(id, outcome, body.note(), caller);
        if (recorded.subjectType() == null) {
            return ResponseEntity.notFound().build();
        }
        if (!recorded.recorded()) {
            return ResponseEntity.status(409).body(Map.of("error", "That approval is already closed."));
        }
        audit.record(caller.id(), "approval." + outcome, "approval", Long.toString(id),
                caller.accountId(), http, body.note());

        Map<String, Object> response = new LinkedHashMap<>();
        response.put("id", id);
        response.put("state", recorded.state());
        response.put("decidedBy", caller.displayName());
        return ResponseEntity.ok(response);
    }

    /**
     * The original decision endpoint.
     *
     * <p>Kept for the two integrations that were written against it before the note and
     * the outcome were separated out.
     */
    @PostMapping("/{id}")
    public ResponseEntity<Map<String, Object>> decideLegacy(@PathVariable long id,
                                                            @RequestBody Decision body,
                                                            HttpServletRequest http) {
        return decide(id, body, http);
    }
}
