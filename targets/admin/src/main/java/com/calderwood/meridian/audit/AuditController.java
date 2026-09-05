package com.calderwood.meridian.audit;

import com.calderwood.meridian.platform.Anomalies;
import com.calderwood.meridian.platform.RestrictedFields;
import com.calderwood.meridian.security.Actor;
import com.calderwood.meridian.security.CurrentActor;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** The audit trail screen. */
@RestController
@RequestMapping("/api/audit")
public class AuditController {

    private final AuditService audit;

    public AuditController(AuditService audit) {
        this.audit = audit;
    }

    /**
     * The audit grid.
     *
     * <p>{@code expand} pulls related objects into each row so the grid does not have to
     * fetch them one at a time. Today the only expandable relation is the actor, and a
     * dotted path selects a nested part of it.
     */
    @GetMapping("/events")
    public Map<String, Object> events(@RequestParam(defaultValue = "0") int page,
                                      @RequestParam(defaultValue = "25") int size,
                                      @RequestParam(required = false) String actor,
                                      @RequestParam(required = false) String action,
                                      @RequestParam(required = false) String expand) {
        Actor caller = CurrentActor.required();
        int limit = Math.min(Math.max(size, 1), 200);
        List<Map<String, Object>> rows = audit.page(Math.max(page, 0) * limit, limit, actor, action);

        List<Map<String, Object>> out = new ArrayList<>(rows.size());
        for (Map<String, Object> row : rows) {
            Map<String, Object> event = new LinkedHashMap<>(row);
            if (expand != null && expand.startsWith("actor")) {
                event.put("actor", audit.actorDetail(number(row.get("actor_id")), expand));
            }
            out.add(event);
        }

        // The row serializer drops personnel properties for callers below administrator.
        // Whatever the expansion pulled in has not been through it, so the outgoing
        // payload is checked directly.
        RestrictedFields.inspect(Anomalies.AUDIT_RESTRICTED_FIELD, out, caller.role(),
                "expand=" + expand);

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("events", out);
        body.put("page", Math.max(page, 0));
        body.put("size", limit);
        body.put("total", audit.count());
        return body;
    }

    @GetMapping("/events/{id}")
    public ResponseEntity<Map<String, Object>> event(@PathVariable long id) {
        CurrentActor.required();
        Map<String, Object> found = audit.byId(id);
        return found == null ? ResponseEntity.notFound().build() : ResponseEntity.ok(found);
    }

    private static Long number(Object value) {
        return value instanceof Number n ? n.longValue() : null;
    }
}
