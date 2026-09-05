package com.calderwood.meridian.support;

import com.calderwood.meridian.security.Actor;
import com.calderwood.meridian.security.CurrentActor;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** The signed-in operator's own record. */
@RestController
@RequestMapping("/api/profile")
public class ProfileController {

    private final JdbcTemplate jdbc;

    public ProfileController(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public record ProfileUpdate(String displayName, String givenName, String familyName) {
    }

    @GetMapping
    public Map<String, Object> profile() {
        Actor caller = CurrentActor.required();
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT s.id, s.email, s.display_name, s.given_name, s.family_name, s.role,"
                        + " s.account_id, s.directory_uid, s.last_seen_at, s.created_at,"
                        + " a.name AS account_name"
                        + " FROM staff s LEFT JOIN accounts a ON a.id = s.account_id"
                        + " WHERE s.id = ?", caller.id());
        Map<String, Object> body = new LinkedHashMap<>(rows.isEmpty() ? Map.of() : rows.get(0));
        body.put("role", caller.role().stored());
        return body;
    }

    /**
     * Change the parts of a profile an operator owns.
     *
     * <p>Only the three name fields; role, account and sign-in address come from the
     * directory and from onboarding, and are not editable here whatever is sent.
     */
    @PatchMapping
    public ResponseEntity<Map<String, Object>> update(@RequestBody ProfileUpdate change) {
        Actor caller = CurrentActor.required();
        if (change == null) {
            return ResponseEntity.badRequest().body(Map.of("error", "Nothing to change."));
        }
        jdbc.update("UPDATE staff SET display_name = COALESCE(?, display_name),"
                        + " given_name = COALESCE(?, given_name),"
                        + " family_name = COALESCE(?, family_name) WHERE id = ?",
                blank(change.displayName()), blank(change.givenName()), blank(change.familyName()),
                caller.id());
        return ResponseEntity.ok(profile());
    }

    private static String blank(String value) {
        return value == null || value.isBlank() ? null : value.trim();
    }
}
