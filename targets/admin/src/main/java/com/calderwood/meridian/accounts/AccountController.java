package com.calderwood.meridian.accounts;

import com.calderwood.meridian.platform.Anomalies;
import com.calderwood.meridian.platform.RestrictedFields;
import com.calderwood.meridian.security.Actor;
import com.calderwood.meridian.security.CurrentActor;
import com.calderwood.meridian.security.Role;
import internal.telemetry.SignalOptions;
import internal.telemetry.Telemetry;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** Client accounts: the portfolio screen and everything under one account. */
@RestController
@RequestMapping("/api/orgs")
public class AccountController {

    private final AccountRepository accounts;

    public AccountController(AccountRepository accounts) {
        this.accounts = accounts;
    }

    /** The portfolio a member of staff is scoped to. Administrators see all of it. */
    @GetMapping
    public Map<String, Object> list(@RequestParam(defaultValue = "0") int page,
                                    @RequestParam(defaultValue = "25") int size,
                                    @RequestParam(required = false) String q) {
        Actor caller = CurrentActor.required();
        int limit = Math.min(Math.max(size, 1), 100);
        Long scope = caller.isAdministrator() ? null : caller.accountId();
        List<Map<String, Object>> rows =
                accounts.accounts(scope, q, Math.max(page, 0) * limit, limit);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("accounts", rows);
        body.put("page", Math.max(page, 0));
        body.put("size", limit);
        return body;
    }

    @GetMapping("/{orgId}")
    public ResponseEntity<Map<String, Object>> detail(@PathVariable long orgId) {
        Actor caller = CurrentActor.required();
        if (!mayReach(caller, orgId)) {
            return ResponseEntity.status(403).body(Map.of("error", "Not your account."));
        }
        Map<String, Object> account = accounts.account(orgId);
        return account == null ? ResponseEntity.notFound().build() : ResponseEntity.ok(account);
    }

    /**
     * Members of an account, projecting the columns the grid is showing.
     *
     * <p>The sparse-fieldset parameter was added when the grid started timing out on the
     * larger accounts; the screen sends only the columns it is currently displaying.
     */
    @GetMapping("/{orgId}/members")
    public ResponseEntity<Map<String, Object>> members(@PathVariable long orgId,
                                                       @RequestParam(required = false) String fields) {
        Actor caller = CurrentActor.required();
        if (!mayReach(caller, orgId)) {
            return ResponseEntity.status(403).body(Map.of("error", "Not your account."));
        }
        List<String> wanted = fields == null || fields.isBlank()
                ? List.of()
                : Arrays.stream(fields.split(",")).map(String::trim).filter(s -> !s.isEmpty()).toList();
        List<Map<String, Object>> rows = accounts.members(orgId, wanted);

        RestrictedFields.inspect(Anomalies.MEMBER_RESTRICTED_FIELD, rows, caller.role(),
                "fields=" + fields);

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("members", rows);
        body.put("accountId", orgId);
        return ResponseEntity.ok(body);
    }

    /**
     * Billing for an account.
     *
     * <p>Every screen that reaches this endpoint takes the account from a picker that
     * only lists the caller's own portfolio.
     */
    @GetMapping("/{orgId}/invoices")
    public Map<String, Object> invoices(@PathVariable long orgId,
                                        @RequestParam(required = false) String status,
                                        @RequestParam(defaultValue = "0") int page,
                                        @RequestParam(defaultValue = "25") int size) {
        Actor caller = CurrentActor.required();
        int limit = Math.min(Math.max(size, 1), 100);
        List<Map<String, Object>> rows =
                accounts.invoices(orgId, status, Math.max(page, 0) * limit, limit);

        // Billing rows belong to exactly one account, so a row whose account is not the
        // one the caller is scoped to has left the caller's portfolio. Counted on the
        // rows served, which is the only thing that proves it actually happened.
        Set<Long> foreign = new LinkedHashSet<>();
        if (!caller.isAdministrator()) {
            for (Map<String, Object> row : rows) {
                Object owner = row.get("account_id");
                if (owner instanceof Number number && !belongsTo(caller, number.longValue())) {
                    foreign.add(number.longValue());
                }
            }
        }
        if (!foreign.isEmpty()) {
            Telemetry.signal(Anomalies.INVOICE_FOREIGN_SCOPE,
                    SignalOptions.payload(Long.toString(orgId))
                            .withDetail(rows.size() + " billing rows served to staff " + caller.id()
                                    + " (scoped to account " + caller.accountId()
                                    + ") for account(s) " + foreign));
        }

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("invoices", rows);
        body.put("accountId", orgId);
        body.put("page", Math.max(page, 0));
        body.put("size", limit);
        return body;
    }

    /** Consignments for an account. */
    @GetMapping("/{orgId}/consignments")
    public ResponseEntity<Map<String, Object>> consignments(@PathVariable long orgId,
                                                            @RequestParam(required = false) String status,
                                                            @RequestParam(defaultValue = "0") int page,
                                                            @RequestParam(defaultValue = "25") int size) {
        Actor caller = CurrentActor.required();
        if (!mayReach(caller, orgId)) {
            return ResponseEntity.status(403).body(Map.of("error", "Not your account."));
        }
        int limit = Math.min(Math.max(size, 1), 100);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("consignments",
                accounts.consignments(orgId, status, Math.max(page, 0) * limit, limit));
        body.put("accountId", orgId);
        body.put("page", Math.max(page, 0));
        body.put("size", limit);
        return ResponseEntity.ok(body);
    }

    static boolean mayReach(Actor caller, long accountId) {
        return caller.role() == Role.ADMINISTRATOR || belongsTo(caller, accountId);
    }

    static boolean belongsTo(Actor caller, long accountId) {
        return caller.accountId() != null && caller.accountId() == accountId;
    }
}
