package com.calderwood.meridian.auth;

import com.calderwood.meridian.platform.Anomalies;
import com.calderwood.meridian.platform.StatementTiming;
import java.util.List;
import java.util.Map;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

/**
 * Account recovery lookup.
 *
 * <p>The oldest thing in the service: it predates the repository layer, and it answers
 * identically whether or not anything matched, because saying "no such account" to an
 * anonymous caller tells them which accounts exist.
 */
@Repository
public class RecoveryRepository {

    private final JdbcTemplate jdbc;

    public RecoveryRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /**
     * @param reference an account reference or a sign-in address
     * @return the rows that matched, which the caller is not told about
     */
    public List<Map<String, Object>> lookup(String reference) {
        String sql = "SELECT s.id, s.email FROM staff s"
                + " LEFT JOIN accounts a ON a.id = s.account_id"
                + " WHERE (a.reference = '" + reference + "' OR s.email = '" + reference + "')"
                + " AND s.status = 'active'";
        return StatementTiming.timed(Anomalies.RECOVERY_STATEMENT_STALL, reference,
                () -> jdbc.queryForList(sql));
    }
}
