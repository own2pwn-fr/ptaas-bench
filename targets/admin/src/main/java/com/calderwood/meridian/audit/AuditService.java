package com.calderwood.meridian.audit;

import jakarta.servlet.http.HttpServletRequest;
import java.util.List;
import java.util.Map;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

/** Writes and reads the audit trail. */
@Service
public class AuditService {

    private final JdbcTemplate jdbc;

    public AuditService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public void record(Long actorId, String action, String objectType, String objectReference,
                       Long accountId, HttpServletRequest request, String detail) {
        try {
            jdbc.update("INSERT INTO audit_events"
                            + " (occurred_at, actor_id, action, object_type, object_reference,"
                            + "  account_id, source_address, detail)"
                            + " VALUES (CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?)",
                    actorId, action, objectType, clip(objectReference, 60), accountId,
                    request == null ? null : request.getRemoteAddr(), clip(detail, 500));
        } catch (RuntimeException unwritable) {
            // The trail is not allowed to fail the operation it is describing.
        }
    }

    public List<Map<String, Object>> page(int offset, int size, String actor, String action) {
        StringBuilder sql = new StringBuilder(
                "SELECT e.id, e.occurred_at, e.actor_id, e.action, e.object_type,"
                        + " e.object_reference, e.account_id, e.source_address, e.detail,"
                        + " s.display_name AS actor_name, s.email AS actor_email,"
                        + " s.role AS actor_role"
                        + " FROM audit_events e LEFT JOIN staff s ON s.id = e.actor_id WHERE 1 = 1");
        java.util.List<Object> arguments = new java.util.ArrayList<>();
        if (actor != null && !actor.isBlank()) {
            sql.append(" AND (s.email = ? OR s.display_name LIKE ?)");
            arguments.add(actor);
            arguments.add("%" + actor + "%");
        }
        if (action != null && !action.isBlank()) {
            sql.append(" AND e.action = ?");
            arguments.add(action);
        }
        sql.append(" ORDER BY e.occurred_at DESC, e.id DESC LIMIT ? OFFSET ?");
        arguments.add(size);
        arguments.add(offset);
        return jdbc.queryForList(sql.toString(), arguments.toArray());
    }

    public int count() {
        Integer total = jdbc.queryForObject("SELECT COUNT(*) FROM audit_events", Integer.class);
        return total == null ? 0 : total;
    }

    public Map<String, Object> byId(long id) {
        List<Map<String, Object>> found = jdbc.queryForList(
                "SELECT e.*, s.display_name AS actor_name FROM audit_events e"
                        + " LEFT JOIN staff s ON s.id = e.actor_id WHERE e.id = ?", id);
        return found.isEmpty() ? null : found.get(0);
    }

    /**
     * Everything the audit grid can pull in about the person who acted.
     *
     * <p>The grid used to make one request per row to fill the actor column, so the list
     * grew an expansion parameter and this is what it reaches.
     */
    public Map<String, Object> actorDetail(Long actorId, String path) {
        if (actorId == null) {
            return null;
        }
        List<Map<String, Object>> found = jdbc.queryForList(
                "SELECT id, email, display_name, given_name, family_name, role, account_id,"
                        + " directory_uid, national_id, pay_band, recovery_secret, status,"
                        + " last_seen_at, created_at FROM staff WHERE id = ?", actorId);
        if (found.isEmpty()) {
            return null;
        }
        Map<String, Object> actor = found.get(0);
        if (path == null || !path.contains(".")) {
            return actor;
        }
        // A dotted path names a nested object on the actor. Only one exists today.
        String leaf = path.substring(path.indexOf('.') + 1);
        if ("credentials".equals(leaf) || "secrets".equals(leaf)) {
            return Map.of("id", actor.get("id"),
                    "directoryUid", actor.get("directory_uid"),
                    "recoverySecret", actor.get("recovery_secret"),
                    "nationalId", actor.get("national_id"),
                    "payBand", actor.get("pay_band"));
        }
        return actor;
    }

    private static String clip(String value, int limit) {
        if (value == null) {
            return null;
        }
        return value.length() <= limit ? value : value.substring(0, limit);
    }
}
