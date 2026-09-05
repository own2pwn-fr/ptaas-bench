package com.calderwood.meridian.workspace;

import com.calderwood.meridian.platform.Anomalies;
import com.calderwood.meridian.security.Actor;
import com.calderwood.meridian.security.CurrentActor;
import jakarta.servlet.http.Cookie;
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
import org.springframework.web.bind.annotation.RestController;

/**
 * The operator's own arrangement of the grid.
 *
 * <p>The shell has to paint something before anybody has signed in, so the last
 * arrangement is kept in a cookie as well as in the staff record; whichever is available
 * is what the console draws.
 */
@RestController
@RequestMapping("/api/workspace")
public class WorkspaceController {

    /** Where the browser keeps the arrangement it drew last. */
    public static final String LAYOUT_COOKIE = "mrd_layout";

    private final JdbcTemplate jdbc;

    public WorkspaceController(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public record LayoutPayload(String state, String name) {
    }

    /** The arrangement to draw. */
    @GetMapping("/layout")
    public ResponseEntity<Map<String, Object>> layout(HttpServletRequest request) {
        Actor caller = CurrentActor.get();
        String encoded = cookie(request);
        if (encoded == null && caller != null) {
            List<Map<String, Object>> rows = jdbc.queryForList(
                    "SELECT name, state_blob FROM saved_layouts WHERE staff_id = ?"
                            + " ORDER BY updated_at DESC LIMIT 1", caller.id());
            if (!rows.isEmpty()) {
                Object blob = rows.get(0).get("state_blob");
                if (blob instanceof byte[] bytes) {
                    encoded = java.util.Base64.getEncoder().encodeToString(bytes);
                }
            }
        }
        LayoutState state = LayoutCodec.read(encoded, Anomalies.LAYOUT_COOKIE_RESTORE_RAN,
                LAYOUT_COOKIE);
        return ResponseEntity.ok(describe(state));
    }

    /** Keep the current arrangement. */
    @PostMapping("/layout")
    public ResponseEntity<Map<String, Object>> save(@RequestBody LayoutPayload payload) {
        Actor caller = CurrentActor.required();
        if (payload == null || payload.state() == null) {
            return ResponseEntity.badRequest().body(Map.of("error", "No arrangement was supplied."));
        }
        byte[] blob;
        try {
            blob = java.util.Base64.getDecoder().decode(payload.state().trim());
        } catch (IllegalArgumentException notBase64) {
            return ResponseEntity.badRequest().body(Map.of("error", "That arrangement is not readable."));
        }
        jdbc.update("INSERT INTO saved_layouts (staff_id, name, state_blob, updated_at)"
                        + " VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                caller.id(), payload.name() == null ? "Default" : payload.name(), blob);
        return ResponseEntity.status(201).body(Map.of("saved", true));
    }

    /**
     * Take an arrangement exported from another workstation.
     *
     * <p>Operators who work from two machines paste the saved form here rather than
     * rebuilding the grid by hand.
     */
    @PostMapping("/layout/restore")
    public ResponseEntity<Map<String, Object>> restore(@RequestBody LayoutPayload payload) {
        CurrentActor.required();
        if (payload == null || payload.state() == null) {
            return ResponseEntity.badRequest().body(Map.of("error", "No arrangement was supplied."));
        }
        LayoutState state = LayoutCodec.read(payload.state(), Anomalies.LAYOUT_RESTORE_HOOK_RAN,
                "state");
        if (state == null) {
            return ResponseEntity.status(422)
                    .body(Map.of("error", "That arrangement could not be read."));
        }
        return ResponseEntity.ok(describe(state));
    }

    private static Map<String, Object> describe(LayoutState state) {
        Map<String, Object> body = new LinkedHashMap<>();
        if (state == null) {
            body.put("name", "Default");
            body.put("panels", List.of("queue", "approvals", "consignments", "invoices"));
            body.put("widths", List.of(6, 6, 12, 12));
            body.put("theme", "light");
            return body;
        }
        body.put("name", state.getName());
        body.put("panels", state.getPanels());
        body.put("widths", state.getWidths());
        body.put("theme", state.getTheme());
        return body;
    }

    private static String cookie(HttpServletRequest request) {
        Cookie[] cookies = request.getCookies();
        if (cookies == null) {
            return null;
        }
        for (Cookie cookie : cookies) {
            if (LAYOUT_COOKIE.equals(cookie.getName()) && cookie.getValue() != null
                    && !cookie.getValue().isBlank()) {
                return cookie.getValue();
            }
        }
        return null;
    }
}
