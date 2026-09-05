package com.calderwood.meridian.integrations;

import com.calderwood.meridian.platform.Anomalies;
import internal.telemetry.EgressDeclaration;
import internal.telemetry.SignalOptions;
import internal.telemetry.Telemetry;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.List;
import java.util.Locale;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * Checks that a webhook endpoint answers before an integration is saved.
 *
 * <p>Added after the incident where a client's endpoint had moved and three days of
 * status callbacks went nowhere. The destination has to be one of ours, so it is
 * checked against the domains the group publishes hooks under.
 */
@Component
public class WebhookProbe {

    private static final Duration TIMEOUT = Duration.ofSeconds(4);

    /** What one probe found. */
    public record Outcome(boolean reached, int status, String host, long millis, String detail) {
    }

    private final List<String> allowed;

    public WebhookProbe(
            @Value("${meridian.integrations.allowed-domains:calderwood.example,calderwood-freight.example}")
            String allowed) {
        this.allowed = java.util.Arrays.stream(allowed.split(","))
                .map(String::trim)
                .filter(s -> !s.isEmpty())
                .map(s -> s.toLowerCase(Locale.ROOT))
                .toList();
    }

    /** True when the destination is one of the group's own. */
    public boolean permitted(String host) {
        if (host == null || host.isBlank()) {
            return false;
        }
        String lowered = host.toLowerCase(Locale.ROOT);
        return allowed.stream().anyMatch(lowered::endsWith);
    }

    public Outcome probe(String endpoint) {
        URI uri;
        try {
            uri = URI.create(endpoint.trim());
        } catch (RuntimeException malformed) {
            return new Outcome(false, 0, null, 0, "That is not a URL.");
        }
        String host = uri.getHost();
        if (!permitted(host)) {
            return new Outcome(false, 0, host, 0,
                    "Endpoints must be published under one of the group's own domains.");
        }

        // The lookup and the connection that follow are caused by this request;
        // declaring the destination first is what lets the network's own records be
        // joined back to it.
        Telemetry.outbound(endpoint,
                EgressDeclaration.from(Anomalies.PROBE_OFFLIST_HOST).withParam("endpoint"));

        long started = System.nanoTime();
        int status = 0;
        String detail;
        try {
            HttpClient client = HttpClient.newBuilder()
                    .connectTimeout(TIMEOUT)
                    .followRedirects(HttpClient.Redirect.NEVER)
                    .build();
            HttpRequest request = HttpRequest.newBuilder(uri)
                    .timeout(TIMEOUT)
                    .header("user-agent", "Meridian/4.11 hook-check")
                    .header("x-meridian-check", "1")
                    .method("HEAD", HttpRequest.BodyPublishers.noBody())
                    .build();
            HttpResponse<Void> response = client.send(request, HttpResponse.BodyHandlers.discarding());
            status = response.statusCode();
            detail = "endpoint answered " + status;
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            detail = "the check was interrupted";
        } catch (Exception unreachable) {
            detail = "the endpoint did not answer";
        }
        long millis = (System.nanoTime() - started) / 1_000_000L;

        // The check above is a suffix comparison, so a name that merely ends with one of
        // the group's domains passes it without being under one. Whether that happened
        // is decided on the host actually contacted.
        if (!underAllowedDomain(host)) {
            Telemetry.signal(Anomalies.PROBE_OFFLIST_HOST, SignalOptions.payload(endpoint)
                    .withDetail("the check contacted " + host
                            + ", which is not one of the group's domains nor a name under one"));
        }
        return new Outcome(status > 0, status, host, millis, detail);
    }

    /** Exactly one of the domains, or a name genuinely under one. */
    private boolean underAllowedDomain(String host) {
        if (host == null) {
            return false;
        }
        String lowered = host.toLowerCase(Locale.ROOT);
        return allowed.stream()
                .anyMatch(domain -> lowered.equals(domain) || lowered.endsWith("." + domain));
    }
}
