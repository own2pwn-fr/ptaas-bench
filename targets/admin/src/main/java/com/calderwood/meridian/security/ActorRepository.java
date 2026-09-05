package com.calderwood.meridian.security;

import java.util.List;
import java.util.Optional;
import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

/** Staff rows, read by id, by sign-in identifier or by directory identifier. */
@Repository
public class ActorRepository {

    private static final String COLUMNS =
            "id, email, display_name, role, account_id, directory_uid, status";

    private final JdbcTemplate jdbc;

    public ActorRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    private static final RowMapper<Actor> MAPPER = (rs, row) -> new Actor(
            rs.getLong("id"),
            rs.getString("email"),
            rs.getString("display_name"),
            Role.of(rs.getString("role")),
            rs.getObject("account_id") == null ? null : rs.getLong("account_id"),
            rs.getString("directory_uid"));

    public Optional<Actor> byId(long id) {
        return one("SELECT " + COLUMNS + " FROM staff WHERE id = ? AND status <> 'suspended'", id);
    }

    public Optional<Actor> byEmail(String email) {
        return one("SELECT " + COLUMNS + " FROM staff WHERE email = ? AND status <> 'suspended'", email);
    }

    public Optional<Actor> byDirectoryUid(String uid) {
        return one("SELECT " + COLUMNS + " FROM staff WHERE directory_uid = ? AND status <> 'suspended'",
                uid);
    }

    private Optional<Actor> one(String sql, Object argument) {
        try {
            return Optional.ofNullable(jdbc.queryForObject(sql, MAPPER, argument));
        } catch (EmptyResultDataAccessException none) {
            return Optional.empty();
        }
    }

    /** Onboarding state: active, suspended, or still the installer's provisioning row. */
    public String statusOf(long id) {
        List<String> found =
                jdbc.queryForList("SELECT status FROM staff WHERE id = ?", String.class, id);
        return found.isEmpty() ? null : found.get(0);
    }

    /** Display name of the client account a member of staff is scoped to. */
    public String accountNameOf(Long accountId) {
        if (accountId == null) {
            return null;
        }
        List<String> found = jdbc.queryForList(
                "SELECT name FROM accounts WHERE id = ?", String.class, accountId);
        return found.isEmpty() ? null : found.get(0);
    }

    public void touchLastSeen(long id) {
        jdbc.update("UPDATE staff SET last_seen_at = CURRENT_TIMESTAMP WHERE id = ?", id);
    }
}
