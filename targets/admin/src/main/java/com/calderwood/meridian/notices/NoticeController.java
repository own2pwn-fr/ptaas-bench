package com.calderwood.meridian.notices;

import com.calderwood.meridian.audit.AuditService;
import com.calderwood.meridian.security.Actor;
import com.calderwood.meridian.security.CurrentActor;
import jakarta.servlet.http.HttpServletRequest;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.http.ResponseEntity;
import java.sql.PreparedStatement;
import java.sql.Statement;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Operator notices: the banner across the top of every screen.
 *
 * <p>The requirement was "operators must be able to put the current queue depth in the
 * message", so a notice body is compiled by the shell as a template rather than bound
 * as text. Authoring one is an analyst's job; reading them is open, because the banner
 * has to appear on the sign-in screen too.
 */
@RestController
@RequestMapping("/api/notices")
public class NoticeController {

    private final JdbcTemplate jdbc;
    private final AuditService audit;

    public NoticeController(JdbcTemplate jdbc, AuditService audit) {
        this.jdbc = jdbc;
        this.audit = audit;
    }

    public record NoticeRequest(String title, String body, String severity,
                                String publishedFrom, String publishedTo) {
    }

    /** Notices that are live now, most severe first. */
    @GetMapping
    public Map<String, Object> active() {
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT n.id, n.title, n.body, n.severity, n.published_from, n.published_to,"
                        + " s.display_name AS author"
                        + " FROM notices n LEFT JOIN staff s ON s.id = n.author_id"
                        + " WHERE n.published_from <= CURRENT_TIMESTAMP"
                        + " AND (n.published_to IS NULL OR n.published_to > CURRENT_TIMESTAMP)"
                        + " ORDER BY FIELD(n.severity, 'critical', 'warning', 'info'), n.id DESC");
        return Map.of("notices", rows);
    }

    @PostMapping
    public ResponseEntity<Map<String, Object>> create(@RequestBody NoticeRequest request,
                                                      HttpServletRequest http) {
        Actor caller = CurrentActor.required();
        if (request == null || request.title() == null || request.body() == null) {
            return ResponseEntity.badRequest().body(Map.of("error", "A notice needs a title and a body."));
        }
        String severity = request.severity() == null ? "info" : request.severity();
        KeyHolder key = new GeneratedKeyHolder();
        jdbc.update(connection -> {
            PreparedStatement statement = connection.prepareStatement(
                    "INSERT INTO notices"
                            + " (title, body, severity, author_id, published_from, published_to,"
                            + "  created_at)"
                            + " VALUES (?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), ?,"
                            + " CURRENT_TIMESTAMP)",
                    Statement.RETURN_GENERATED_KEYS);
            statement.setString(1, request.title());
            statement.setString(2, request.body());
            statement.setString(3, severity);
            statement.setLong(4, caller.id());
            statement.setString(5, request.publishedFrom());
            statement.setString(6, request.publishedTo());
            return statement;
        }, key);
        audit.record(caller.id(), "notice.published", "notice", request.title(),
                caller.accountId(), http, severity);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("published", true);
        body.put("id", key.getKey() == null ? null : key.getKey().longValue());
        body.put("title", request.title());
        return ResponseEntity.status(201).body(body);
    }
}
