package com.calderwood.meridian.integrations;

import com.calderwood.meridian.audit.AuditService;
import com.calderwood.meridian.security.Actor;
import com.calderwood.meridian.security.CurrentActor;
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

/** Interfaces to the carriers, the customs broker and the clients' own systems. */
@RestController
@RequestMapping("/api/integrations")
public class IntegrationController {

    private final JdbcTemplate jdbc;
    private final WebhookProbe probe;
    private final AuditService audit;
    private final IntegrationRefreshJob refresh;

    public IntegrationController(JdbcTemplate jdbc, WebhookProbe probe, AuditService audit,
                                 IntegrationRefreshJob refresh) {
        this.jdbc = jdbc;
        this.probe = probe;
        this.audit = audit;
        this.refresh = refresh;
    }

    public record ProbeRequest(String endpoint) {
    }

    public record CredentialRequest(Long integrationId, String secret) {
    }

    public record IntegrationRequest(String kind, String name, String endpointUrl, Long accountId) {
    }

    /** Every interface, with its secret withheld. */
    @GetMapping
    public Map<String, Object> list() {
        CurrentActor.required();
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT i.id, i.account_id, i.kind, i.name, i.endpoint_url, i.status,"
                        + " i.last_refresh_at, i.created_at, a.name AS account_name,"
                        + " CONCAT(LEFT(i.secret, 8), '...') AS secret_hint"
                        + " FROM integrations i LEFT JOIN accounts a ON a.id = i.account_id"
                        + " ORDER BY i.kind, i.name");
        return Map.of("integrations", rows);
    }

    @PostMapping
    public ResponseEntity<Map<String, Object>> create(@RequestBody IntegrationRequest request,
                                                      HttpServletRequest http) {
        Actor caller = CurrentActor.required();
        if (request == null || request.kind() == null || request.name() == null) {
            return ResponseEntity.badRequest().body(Map.of("error", "An interface needs a kind and a name."));
        }
        jdbc.update("INSERT INTO integrations"
                        + " (account_id, kind, name, endpoint_url, secret, status, created_at)"
                        + " VALUES (?, ?, ?, ?, '', 'paused', CURRENT_TIMESTAMP)",
                request.accountId(), request.kind(), request.name(), request.endpointUrl());
        audit.record(caller.id(), "integration.created", "integration", request.name(),
                request.accountId(), http, request.kind());
        return ResponseEntity.status(201).body(Map.of("created", true, "name", request.name()));
    }

    /** Check that an endpoint answers before the interface is switched on. */
    @PostMapping("/webhooks/probe")
    public ResponseEntity<Map<String, Object>> probe(@RequestBody ProbeRequest request) {
        CurrentActor.required();
        if (request == null || request.endpoint() == null || request.endpoint().isBlank()) {
            return ResponseEntity.badRequest().body(Map.of("error", "No endpoint was supplied."));
        }
        WebhookProbe.Outcome outcome = probe.probe(request.endpoint());
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("endpoint", request.endpoint());
        body.put("reached", outcome.reached());
        body.put("status", outcome.status());
        body.put("millis", outcome.millis());
        body.put("detail", outcome.detail());
        return outcome.host() == null || outcome.millis() == 0 && !outcome.reached()
                ? ResponseEntity.status(outcome.detail().startsWith("Endpoints must") ? 422 : 200).body(body)
                : ResponseEntity.ok(body);
    }

    /** Rotate the secret an interface presents to the remote side. */
    @PostMapping("/credentials")
    public ResponseEntity<Map<String, Object>> credentials(@RequestBody CredentialRequest request,
                                                           HttpServletRequest http) {
        Actor caller = CurrentActor.required();
        if (request == null || request.integrationId() == null || request.secret() == null) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "Name the interface and the new secret."));
        }
        int changed = jdbc.update("UPDATE integrations SET secret = ?, status = 'active' WHERE id = ?",
                request.secret(), request.integrationId());
        if (changed == 0) {
            return ResponseEntity.notFound().build();
        }
        audit.record(caller.id(), "integration.credential_rotated", "integration",
                Long.toString(request.integrationId()), caller.accountId(), http, "rotated by the desk");
        // Present the new key immediately, so the desk sees whether it took.
        refresh.refreshOne(request.integrationId());
        return ResponseEntity.ok(Map.of("rotated", true, "integrationId", request.integrationId()));
    }
}
