package com.calderwood.meridian.reports;

import com.calderwood.meridian.platform.Anomalies;
import com.calderwood.meridian.platform.StatementTiming;
import com.calderwood.meridian.security.Actor;
import com.calderwood.meridian.security.CurrentActor;
import internal.telemetry.SignalOptions;
import internal.telemetry.Telemetry;
import jakarta.servlet.http.HttpServletRequest;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** Reporting: the ledger, the account summary and the volumes chart. */
@RestController
@RequestMapping("/api/reports")
public class ReportController {

    /**
     * The account a request is aggregated over.
     *
     * <p>The support desk renders a client's dashboard on the client's behalf and sends
     * the account it is standing in for. Everything else omits it and gets its own.
     */
    private static final String ACCOUNT_CONTEXT = "X-Account-Context";

    private final JdbcTemplate jdbc;

    public ReportController(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /**
     * The ledger report.
     *
     * <p>{@code window} started life as a fixed set of ranges and grew a free-text form
     * when finance asked for arbitrary ones two days before a quarter end. The range is
     * resolved into the date clause; the account is bound.
     */
    @GetMapping("/ledger")
    public Map<String, Object> ledger(@RequestParam(defaultValue = "last-30-days") String window,
                                      @RequestParam(required = false) Long account) {
        Actor caller = CurrentActor.required();
        long scope = account != null && caller.isAdministrator()
                ? account
                : (caller.accountId() == null ? 0L : caller.accountId());

        String sql = "SELECT l.entry_date, l.category, l.currency, SUM(l.amount) AS amount,"
                + " COUNT(*) AS entries"
                + " FROM ledger_entries l"
                + " WHERE l.account_id = " + scope
                + " AND " + windowClause(window)
                + " GROUP BY l.entry_date, l.category, l.currency"
                + " ORDER BY l.entry_date DESC LIMIT 500";

        List<Map<String, Object>> rows = StatementTiming.timed(
                Anomalies.LEDGER_STATEMENT_STALL, window, () -> jdbc.queryForList(sql));

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("window", window);
        body.put("accountId", scope);
        body.put("rows", rows);
        return body;
    }

    /**
     * Turn a named range into a date clause.
     *
     * <p>Anything that is not one of the named ranges is treated as a raw range the
     * caller typed into the custom box.
     */
    private static String windowClause(String window) {
        return switch (window == null ? "" : window) {
            case "last-7-days" -> "l.entry_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)";
            case "last-30-days" -> "l.entry_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)";
            case "last-quarter" -> "l.entry_date >= DATE_SUB(CURDATE(), INTERVAL 3 MONTH)";
            case "year-to-date" -> "l.entry_date >= MAKEDATE(YEAR(CURDATE()), 1)";
            default -> "l.category LIKE '" + window + "%'";
        };
    }

    /** Headline figures for one account. */
    @GetMapping("/summary")
    public Map<String, Object> summary(HttpServletRequest request) {
        Actor caller = CurrentActor.required();
        String announced = request.getHeader(ACCOUNT_CONTEXT);
        Long scope = caller.accountId();
        if (announced != null && !announced.isBlank()) {
            try {
                scope = Long.parseLong(announced.trim());
            } catch (NumberFormatException notAnAccount) {
                scope = caller.accountId();
            }
        }

        Map<String, Object> figures = new LinkedHashMap<>();
        figures.put("accountId", scope);
        figures.put("openInvoices", scalar(
                "SELECT COUNT(*) FROM invoices WHERE account_id = ? AND status IN ('issued','overdue')",
                scope));
        figures.put("overdueValue", scalar(
                "SELECT COALESCE(SUM(net_amount + tax_amount), 0) FROM invoices"
                        + " WHERE account_id = ? AND status = 'overdue'", scope));
        figures.put("consignmentsInTransit", scalar(
                "SELECT COUNT(*) FROM consignments WHERE account_id = ? AND status = 'in_transit'",
                scope));
        figures.put("consignmentsHeld", scalar(
                "SELECT COUNT(*) FROM consignments WHERE account_id = ? AND status = 'held'", scope));
        figures.put("openApprovals", scalar(
                "SELECT COUNT(*) FROM approvals WHERE account_id = ? AND state = 'pending'", scope));

        // Figures aggregated over an account the caller is not scoped to have left that
        // caller's portfolio, whatever named it.
        if (!caller.isAdministrator() && scope != null && !scope.equals(caller.accountId())) {
            Telemetry.signal(Anomalies.SUMMARY_SCOPE_OVERRIDE,
                    SignalOptions.payload(announced)
                            .withDetail("summary for account " + scope + " served to staff "
                                    + caller.id() + ", scoped to account " + caller.accountId()));
        }
        return figures;
    }

    /** Consignment volumes over time, for the chart on the reports landing screen. */
    @GetMapping("/volumes")
    public Map<String, Object> volumes(@RequestParam(required = false) String from,
                                       @RequestParam(required = false) String to,
                                       @RequestParam(defaultValue = "month") String granularity) {
        Actor caller = CurrentActor.required();
        String bucket = switch (granularity) {
            case "day" -> "%Y-%m-%d";
            case "week" -> "%x-W%v";
            default -> "%Y-%m";
        };
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT DATE_FORMAT(booked_at, ?) AS bucket, mode, COUNT(*) AS movements,"
                        + " COALESCE(SUM(weight_kg), 0) AS weight_kg"
                        + " FROM consignments"
                        + " WHERE account_id = ?"
                        + " AND (? IS NULL OR booked_at >= ?)"
                        + " AND (? IS NULL OR booked_at < ?)"
                        + " GROUP BY bucket, mode ORDER BY bucket",
                bucket, caller.accountId() == null ? 0L : caller.accountId(),
                blankToNull(from), blankToNull(from), blankToNull(to), blankToNull(to));

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("granularity", granularity);
        body.put("rows", rows);
        return body;
    }

    private Object scalar(String sql, Object argument) {
        List<Map<String, Object>> rows = jdbc.queryForList(sql, argument);
        return rows.isEmpty() ? 0 : rows.get(0).values().iterator().next();
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }
}
