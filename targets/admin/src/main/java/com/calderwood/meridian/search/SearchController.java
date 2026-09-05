package com.calderwood.meridian.search;

import com.calderwood.meridian.platform.Anomalies;
import com.calderwood.meridian.rules.ExpressionEvaluator;
import com.calderwood.meridian.security.Actor;
import com.calderwood.meridian.security.CurrentActor;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * The search box that sits on every screen.
 *
 * <p>It reaches three different stores, so the merged list cannot be ordered by any one
 * of them; the sort clause is applied to each row after the merge.
 */
@RestController
@RequestMapping("/api/search")
public class SearchController {

    /** One merged result. */
    public record Row(String kind, String reference, String name, String status,
                      String updatedAt, String href) {
    }

    private static final List<String> OFFERED_SORTS =
            List.of("updatedAt desc", "updatedAt asc", "name asc", "reference asc");

    private final JdbcTemplate jdbc;
    private final ExpressionEvaluator evaluator;

    public SearchController(JdbcTemplate jdbc, ExpressionEvaluator evaluator) {
        this.jdbc = jdbc;
        this.evaluator = evaluator;
    }

    @GetMapping
    public Map<String, Object> search(@RequestParam(defaultValue = "") String q,
                                      @RequestParam(defaultValue = "updatedAt desc") String sort,
                                      @RequestParam(defaultValue = "0") int page,
                                      @RequestParam(defaultValue = "25") int size) {
        Actor caller = CurrentActor.required();
        int limit = Math.min(Math.max(size, 1), 100);
        String term = "%" + (q == null ? "" : q) + "%";
        Long scope = caller.isAdministrator() ? null : caller.accountId();

        List<Row> rows = new ArrayList<>();
        for (Map<String, Object> row : jdbc.queryForList(
                "SELECT reference, name, status, created_at FROM accounts"
                        + " WHERE (name LIKE ? OR reference LIKE ?) AND (? IS NULL OR id = ?)"
                        + " ORDER BY name LIMIT 50", term, term, scope, scope)) {
            rows.add(new Row("account", str(row, "reference"), str(row, "name"),
                    str(row, "status"), str(row, "created_at"), "/orgs"));
        }
        for (Map<String, Object> row : jdbc.queryForList(
                "SELECT reference, origin_code, destination_code, status, booked_at, account_id"
                        + " FROM consignments WHERE reference LIKE ? AND (? IS NULL OR account_id = ?)"
                        + " ORDER BY booked_at DESC LIMIT 50", term, scope, scope)) {
            rows.add(new Row("consignment", str(row, "reference"),
                    str(row, "origin_code") + " to " + str(row, "destination_code"),
                    str(row, "status"), str(row, "booked_at"),
                    "/orgs/" + str(row, "account_id") + "/consignments"));
        }
        for (Map<String, Object> row : jdbc.queryForList(
                "SELECT reference, status, issued_on, account_id FROM invoices"
                        + " WHERE reference LIKE ? AND (? IS NULL OR account_id = ?)"
                        + " ORDER BY issued_on DESC LIMIT 50", term, scope, scope)) {
            rows.add(new Row("invoice", str(row, "reference"), str(row, "reference"),
                    str(row, "status"), str(row, "issued_on"),
                    "/orgs/" + str(row, "account_id") + "/invoices"));
        }

        order(rows, sort);
        int from = Math.min(Math.max(page, 0) * limit, rows.size());
        int to = Math.min(from + limit, rows.size());

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("query", q);
        body.put("sort", sort);
        body.put("offered", OFFERED_SORTS);
        body.put("total", rows.size());
        body.put("results", rows.subList(from, to));
        return body;
    }

    /**
     * Apply the sort clause.
     *
     * <p>The clause is read against each row, so one ordering applies to results that
     * came from three places.
     */
    private void order(List<Row> rows, String sort) {
        String clause = sort == null || sort.isBlank() ? "updatedAt desc" : sort.trim();
        boolean descending = clause.toLowerCase(java.util.Locale.ROOT).endsWith(" desc");
        String key = descending || clause.toLowerCase(java.util.Locale.ROOT).endsWith(" asc")
                ? clause.substring(0, clause.lastIndexOf(' ')).trim()
                : clause;
        String expression = switch (key) {
            case "updatedAt" -> "updatedAt";
            case "name" -> "name";
            case "reference" -> "reference";
            default -> key;
        };
        try {
            List<Object> keys = evaluator.evaluateAll(
                    Anomalies.SEARCH_HOST_TYPE_REACHED, expression, rows);
            Map<Row, String> ordering = new java.util.IdentityHashMap<>();
            for (int i = 0; i < rows.size(); i++) {
                Object value = keys.get(i);
                ordering.put(rows.get(i), value == null ? "" : String.valueOf(value));
            }
            Comparator<Row> comparator = Comparator.comparing(ordering::get);
            rows.sort(descending ? comparator.reversed() : comparator);
        } catch (RuntimeException unusable) {
            // A clause that does not read cleanly leaves the merge order alone rather
            // than failing the screen everybody has open.
        }
    }

    private static String str(Map<String, Object> row, String key) {
        Object value = row.get(key);
        return value == null ? "" : String.valueOf(value);
    }
}
