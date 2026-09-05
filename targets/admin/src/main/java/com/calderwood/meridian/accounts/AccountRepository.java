package com.calderwood.meridian.accounts;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

/** Client accounts and everything hanging off one. */
@Repository
public class AccountRepository {

    /**
     * Member properties the grid may project, and the column each one reads.
     *
     * <p>The grid's column picker sends the selection as a list of property names, so the
     * names have to be resolved to columns here rather than pasted into the statement.
     */
    private static final Map<String, String> MEMBER_COLUMNS = Map.ofEntries(
            Map.entry("id", "id"),
            Map.entry("email", "email"),
            Map.entry("displayname", "display_name"),
            Map.entry("givenname", "given_name"),
            Map.entry("familyname", "family_name"),
            Map.entry("role", "role"),
            Map.entry("status", "status"),
            Map.entry("directoryuid", "directory_uid"),
            Map.entry("lastseenat", "last_seen_at"),
            Map.entry("createdat", "created_at"),
            Map.entry("nationalid", "national_id"),
            Map.entry("payband", "pay_band"),
            Map.entry("recoverysecret", "recovery_secret"),
            Map.entry("passwordhash", "password_hash"));

    private static final List<String> DEFAULT_MEMBER_FIELDS = List.of("id", "displayName", "role");

    private final JdbcTemplate jdbc;

    public AccountRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public List<Map<String, Object>> accounts(Long scope, String term, int offset, int size) {
        StringBuilder sql = new StringBuilder(
                "SELECT id, reference, name, legal_name, country_code, tier, status, onboarded_on"
                        + " FROM accounts WHERE 1 = 1");
        List<Object> arguments = new ArrayList<>();
        if (scope != null) {
            sql.append(" AND id = ?");
            arguments.add(scope);
        }
        if (term != null && !term.isBlank()) {
            sql.append(" AND (name LIKE ? OR reference LIKE ?)");
            arguments.add("%" + term + "%");
            arguments.add("%" + term + "%");
        }
        sql.append(" ORDER BY name LIMIT ? OFFSET ?");
        arguments.add(size);
        arguments.add(offset);
        return jdbc.queryForList(sql.toString(), arguments.toArray());
    }

    public Map<String, Object> account(long id) {
        List<Map<String, Object>> found = jdbc.queryForList(
                "SELECT id, reference, name, legal_name, country_code, tier, status, onboarded_on,"
                        + " created_at FROM accounts WHERE id = ?", id);
        return found.isEmpty() ? null : found.get(0);
    }

    /**
     * Members of one account, projecting the properties the caller named.
     *
     * <p>Unknown names are dropped rather than refused: the grid remembers a column
     * selection between releases, and a removed column should not break the screen.
     */
    public List<Map<String, Object>> members(long accountId, List<String> fields) {
        List<String> wanted = fields == null || fields.isEmpty() ? DEFAULT_MEMBER_FIELDS : fields;
        Map<String, String> projection = new LinkedHashMap<>();
        for (String field : wanted) {
            String column = MEMBER_COLUMNS.get(field.trim().toLowerCase(Locale.ROOT));
            if (column != null) {
                projection.put(field.trim(), column);
            }
        }
        if (projection.isEmpty()) {
            for (String field : DEFAULT_MEMBER_FIELDS) {
                projection.put(field, MEMBER_COLUMNS.get(field.toLowerCase(Locale.ROOT)));
            }
        }
        String columns = String.join(", ", projection.values());
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT " + columns + " FROM staff WHERE account_id = ? ORDER BY display_name",
                accountId);

        // Re-key the rows onto the property names the caller used, so the grid gets back
        // exactly the columns it asked for.
        List<Map<String, Object>> out = new ArrayList<>(rows.size());
        for (Map<String, Object> row : rows) {
            Map<String, Object> member = new LinkedHashMap<>();
            projection.forEach((property, column) -> member.put(property, row.get(column)));
            out.add(member);
        }
        return out;
    }

    public List<Map<String, Object>> invoices(long accountId, String status, int offset, int size) {
        StringBuilder sql = new StringBuilder(
                "SELECT id, account_id, reference, issued_on, due_on, currency, net_amount,"
                        + " tax_amount, status FROM invoices WHERE account_id = ?");
        List<Object> arguments = new ArrayList<>();
        arguments.add(accountId);
        if (status != null && !status.isBlank()) {
            sql.append(" AND status = ?");
            arguments.add(status);
        }
        sql.append(" ORDER BY issued_on DESC, id DESC LIMIT ? OFFSET ?");
        arguments.add(size);
        arguments.add(offset);
        return jdbc.queryForList(sql.toString(), arguments.toArray());
    }

    public List<Map<String, Object>> consignments(long accountId, String status, int offset, int size) {
        StringBuilder sql = new StringBuilder(
                "SELECT id, account_id, reference, origin_code, destination_code, mode, weight_kg,"
                        + " volume_m3, status, booked_at, cleared_at FROM consignments"
                        + " WHERE account_id = ?");
        List<Object> arguments = new ArrayList<>();
        arguments.add(accountId);
        if (status != null && !status.isBlank()) {
            sql.append(" AND status = ?");
            arguments.add(status);
        }
        sql.append(" ORDER BY booked_at DESC, id DESC LIMIT ? OFFSET ?");
        arguments.add(size);
        arguments.add(offset);
        return jdbc.queryForList(sql.toString(), arguments.toArray());
    }
}
