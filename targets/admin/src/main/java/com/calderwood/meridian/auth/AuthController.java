package com.calderwood.meridian.auth;

import com.calderwood.meridian.audit.AuditService;
import com.calderwood.meridian.directory.DirectoryClient;
import com.calderwood.meridian.platform.Anomalies;
import com.calderwood.meridian.security.Actor;
import com.calderwood.meridian.security.ActorRepository;
import com.calderwood.meridian.security.CurrentActor;
import com.calderwood.meridian.security.SessionFilter;
import com.calderwood.meridian.security.TokenCodec;
import internal.telemetry.SignalOptions;
import internal.telemetry.Telemetry;
import jakarta.servlet.http.HttpServletRequest;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseCookie;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** Sign-in, sign-out, account recovery and the published verification key. */
@RestController
@RequestMapping("/api")
public class AuthController {

    private final DirectoryClient directory;
    private final ActorRepository actors;
    private final TokenCodec tokens;
    private final RecoveryRepository recovery;
    private final AuditService audit;

    public AuthController(DirectoryClient directory, ActorRepository actors, TokenCodec tokens,
                          RecoveryRepository recovery, AuditService audit) {
        this.directory = directory;
        this.actors = actors;
        this.tokens = tokens;
        this.recovery = recovery;
        this.audit = audit;
    }

    public record Credentials(String email, String password) {
    }

    public record Reference(String reference) {
    }

    @PostMapping("/auth/login")
    public ResponseEntity<Map<String, Object>> login(@RequestBody Credentials credentials,
                                                     HttpServletRequest request) {
        String email = credentials == null || credentials.email() == null ? "" : credentials.email();
        String password = credentials == null || credentials.password() == null
                ? "" : credentials.password();

        Optional<DirectoryClient.Match> match = directory.signIn(email, password);
        if (match.isEmpty()) {
            audit.record(null, "sign_in.refused", "staff", email, null, request,
                    "no directory entry matched");
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Map.of("error", "Those details did not match an account."));
        }

        Optional<Actor> resolved = actors.byDirectoryUid(match.get().uid());
        if (resolved.isEmpty()) {
            // A directory entry with no console account: somebody who works here but has
            // never been onboarded onto this system.
            return ResponseEntity.status(HttpStatus.FORBIDDEN)
                    .body(Map.of("error", "This account has no console access."));
        }
        Actor actor = resolved.get();

        if ("provisioning".equals(actors.statusOf(actor.id()))) {
            // The account the installer creates so that the first administrator can be
            // added. It is supposed to be disabled once handover is done, and every use
            // of it after that point is worth seeing.
            Telemetry.signal(Anomalies.FACTORY_ACCOUNT_SIGNED_IN,
                    SignalOptions.payload(email)
                            .withDetail("session issued for the provisioning account "
                                    + actor.email() + " (staff " + actor.id() + ")"));
        }

        actors.touchLastSeen(actor.id());
        audit.record(actor.id(), "sign_in.accepted", "staff", actor.email(), actor.accountId(),
                request, "console");

        String token = tokens.issue(actor);
        ResponseCookie cookie = ResponseCookie.from(SessionFilter.COOKIE, token)
                .httpOnly(true)
                .secure(false)
                .sameSite("Strict")
                .path("/")
                .maxAge(TokenCodec.LIFETIME_SECONDS)
                .build();
        return ResponseEntity.ok()
                .header(HttpHeaders.SET_COOKIE, cookie.toString())
                .body(context(actor, true));
    }

    @PostMapping("/auth/logout")
    public ResponseEntity<Map<String, Object>> logout() {
        ResponseCookie cleared = ResponseCookie.from(SessionFilter.COOKIE, "")
                .httpOnly(true).sameSite("Strict").path("/").maxAge(0).build();
        return ResponseEntity.ok()
                .header(HttpHeaders.SET_COOKIE, cleared.toString())
                .body(Map.of("authenticated", false));
    }

    /**
     * Start account recovery.
     *
     * <p>Always accepted. The desk gets the outcome; the caller does not, because a
     * different answer for a reference that exists is a list of the references that do.
     */
    @PostMapping("/auth/recover")
    public Map<String, Object> recover(@RequestBody Reference body) {
        String reference = body == null || body.reference() == null ? "" : body.reference();
        recovery.lookup(reference);
        return Map.of("accepted", true,
                "message", "If that reference matches an account, the desk has been notified.");
    }

    /**
     * The verification key for sessions this console issues.
     *
     * <p>Published so the reporting service and the carrier gateway can accept a session
     * an operator already holds instead of asking them to sign in again.
     */
    @GetMapping("/auth/keys")
    public Map<String, Object> keys() {
        Map<String, Object> key = new LinkedHashMap<>();
        key.put("kid", tokens.keyId());
        key.put("alg", "RS256");
        key.put("use", "sig");
        key.put("pem", tokens.publicKeyPem());
        return Map.of("keys", List.of(key));
    }

    /** Who the shell is talking to, and what it is allowed to paint. */
    @GetMapping("/session/context")
    public Map<String, Object> sessionContext() {
        Actor actor = CurrentActor.get();
        return actor == null
                ? Map.of("authenticated", false, "features", List.of())
                : context(actor, true);
    }

    private Map<String, Object> context(Actor actor, boolean authenticated) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("authenticated", authenticated);
        out.put("subjectId", Long.toString(actor.id()));
        out.put("displayName", actor.displayName());
        out.put("role", actor.role().stored());
        out.put("accountId", actor.accountId());
        out.put("accountName", actors.accountNameOf(actor.accountId()));
        out.put("features", features(actor));
        return out;
    }

    private static List<String> features(Actor actor) {
        return switch (actor.role()) {
            case VIEWER -> List.of("orgs", "reports", "directory", "audit", "notices");
            case ANALYST -> List.of("orgs", "reports", "directory", "audit", "notices", "tariffs",
                    "intake", "exports", "rules", "approvals", "notifications", "workspace");
            case ADMINISTRATOR -> List.of("orgs", "reports", "directory", "audit", "notices",
                    "tariffs", "intake", "exports", "rules", "approvals", "notifications",
                    "workspace", "integrations", "imports", "settings");
        };
    }
}
