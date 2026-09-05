package com.calderwood.meridian.approvals;

import com.calderwood.meridian.platform.Anomalies;
import com.calderwood.meridian.security.Actor;
import internal.telemetry.SignalOptions;
import internal.telemetry.Telemetry;
import java.util.List;
import java.util.Map;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Recording a decision.
 *
 * <p>In one transaction, because the state change and the stamp of who made it have to
 * land together: an approval that says approved with nobody against it is worse than one
 * that is still pending.
 */
@Service
public class ApprovalService {

    /** What the update did. */
    public record Outcome(boolean recorded, String state, String subjectType) {
    }

    private final JdbcTemplate jdbc;

    public ApprovalService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Transactional
    public Outcome decide(long id, String decision, String note, Actor caller) {
        List<Map<String, Object>> found = jdbc.queryForList(
                "SELECT id, subject_type, state FROM approvals WHERE id = ? FOR UPDATE", id);
        if (found.isEmpty()) {
            return new Outcome(false, null, null);
        }
        String current = String.valueOf(found.get(0).get("state"));
        String subjectType = String.valueOf(found.get(0).get("subject_type"));
        if (!"pending".equals(current)) {
            return new Outcome(false, current, subjectType);
        }
        String state = "approve".equals(decision) ? "approved" : "rejected";
        int changed = jdbc.update(
                "UPDATE approvals SET state = ?, decided_by = ?, decided_at = CURRENT_TIMESTAMP,"
                        + " note = ? WHERE id = ? AND state = 'pending'",
                state, caller.id(), note, id);
        if (changed == 0) {
            return new Outcome(false, current, subjectType);
        }

        // Deciding an approval is an administrator's job. A decision that has actually
        // been written by anybody else means the record moved on the say-so of a caller
        // who is not allowed to move it, which is an alert rather than a line in the
        // quarterly report.
        if (!caller.isAdministrator()) {
            Telemetry.signal(Anomalies.APPROVAL_ROLE_GATE_MISSED,
                    SignalOptions.payload(decision)
                            .withDetail("approval " + id + " (" + subjectType + ") recorded as "
                                    + state + " by staff " + caller.id() + " holding "
                                    + caller.role().stored()));
        }
        return new Outcome(true, state, subjectType);
    }
}
