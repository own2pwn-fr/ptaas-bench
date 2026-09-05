package com.calderwood.meridian.integrations;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Re-presents each interface's credentials to the far side on a schedule.
 *
 * <p>The carriers expire a session after fifteen minutes of silence, so this keeps them
 * warm. One carrier spent a fortnight insisting it had never received our callbacks, and
 * the quickest way to settle that argument was to print what we send. The line has been
 * there ever since.
 */
@Component
public class IntegrationRefreshJob {

    private static final Logger log = LoggerFactory.getLogger(IntegrationRefreshJob.class);
    private static final Duration TIMEOUT = Duration.ofSeconds(3);

    private final JdbcTemplate jdbc;

    public IntegrationRefreshJob(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /**
     * Re-present one interface's credentials now.
     *
     * <p>Called straight after a rotation: waiting for the next tick means up to three
     * minutes during which the far side still holds the old key, and that window is
     * exactly when the desk is watching to see whether the rotation worked.
     */
    public void refreshOne(long integrationId) {
        try {
            present(jdbc.queryForList(
                    "SELECT id, kind, name, endpoint_url, secret FROM integrations"
                            + " WHERE id = ? AND secret <> ''", integrationId));
        } catch (RuntimeException unavailable) {
            log.warn("immediate refresh of interface {} did not run", integrationId);
        }
    }

    @Scheduled(initialDelayString = "${meridian.integrations.refresh-delay-ms:20000}",
            fixedDelayString = "${meridian.integrations.refresh-interval-ms:180000}")
    public void refresh() {
        List<Map<String, Object>> rows;
        try {
            rows = jdbc.queryForList(
                    "SELECT id, kind, name, endpoint_url, secret FROM integrations"
                            + " WHERE status = 'active' AND secret <> '' ORDER BY id");
        } catch (RuntimeException notReady) {
            return;
        }
        present(rows);
    }

    private void present(List<Map<String, Object>> rows) {
        for (Map<String, Object> row : rows) {
            String name = String.valueOf(row.get("name"));
            String endpoint = String.valueOf(row.get("endpoint_url"));
            String secret = String.valueOf(row.get("secret"));
            String body = "{\"grant\":\"refresh\",\"client\":\"meridian\",\"key\":\"" + secret + "\"}";

            log.info("refresh {} -> POST {} {}", name, endpoint, body);
            try {
                HttpClient client = HttpClient.newBuilder().connectTimeout(TIMEOUT).build();
                HttpRequest request = HttpRequest.newBuilder(URI.create(endpoint))
                        .timeout(TIMEOUT)
                        .header("content-type", "application/json")
                        .POST(HttpRequest.BodyPublishers.ofString(body))
                        .build();
                HttpResponse<Void> response =
                        client.send(request, HttpResponse.BodyHandlers.discarding());
                jdbc.update("UPDATE integrations SET last_refresh_at = CURRENT_TIMESTAMP,"
                        + " status = ? WHERE id = ?",
                        response.statusCode() < 400 ? "active" : "error", row.get("id"));
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                return;
            } catch (Exception unreachable) {
                log.warn("refresh {} did not complete: {}", name, unreachable.getMessage());
            }
        }
    }
}
