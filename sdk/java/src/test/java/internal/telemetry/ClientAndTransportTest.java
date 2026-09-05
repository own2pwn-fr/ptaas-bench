package internal.telemetry;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;

class ClientAndTransportTest {

    private static TelemetryConfig config(int queueMax) {
        return TelemetryConfig.builder()
                .service("admin")
                .endpoint("http://otel-collector:8900")
                .enabled(true)
                .queueMax(queueMax)
                .batchMax(50)
                .flushInterval(Duration.ofMillis(10))
                .build();
    }

    @Test
    void aSignalIsShapedTheWayTheCollectorExpects() {
        List<Map<String, Object>> exported = new CopyOnWriteArrayList<>();
        TelemetryClient client = new TelemetryClient(config(1000));
        client.sender((path, batch) -> exported.addAll(batch));

        client.signal("console.workspace.layout.restore_hook_ran",
                SignalOptions.payload("rO0ABX...").withDetail("restore hook ran"));
        client.flush(Duration.ofSeconds(2));

        Map<String, Object> record = exported.get(0);
        assertEquals("signal", record.get("type"));
        assertEquals("admin", record.get("app"));
        assertEquals("console.workspace.layout.restore_hook_ran", record.get("signal"));
        @SuppressWarnings("unchecked")
        Map<String, Object> attributes = (Map<String, Object>) record.get("attributes");
        assertEquals("rO0ABX...", attributes.get("payload"));
        assertEquals("restore hook ran", attributes.get("detail"));
        assertNotNull(record.get("ts"));
        client.close(Duration.ofMillis(200));
    }

    @Test
    void aNameThatIsNotMetricShapedIsCountedAndDropped() {
        // A record the collector would refuse is worse than none: it disappears silently
        // at the far end and the counter here is the only place the mistake shows.
        List<Map<String, Object>> exported = new CopyOnWriteArrayList<>();
        TelemetryClient client = new TelemetryClient(config(1000));
        client.sender((path, batch) -> exported.addAll(batch));

        client.signal("Console.Workspace");
        client.signal("noDots");
        client.signal("console.workspace.layout.restore_hook_ran");
        client.flush(Duration.ofSeconds(2));

        assertEquals(1, exported.size());
        assertEquals(2L, client.stats().get("invalid_signals"));
        client.close(Duration.ofMillis(200));
    }

    @Test
    void everySignalPathIsATotalFunction() {
        // Instrumentation that can fail a request is worse than no instrumentation.
        TelemetryClient client = new TelemetryClient(config(1000));
        client.sender((path, batch) -> {
            throw new IllegalStateException("collector refused");
        });
        client.signal("console.a.b");
        client.signal(null);
        client.note(null);
        client.outbound(null, (EgressDeclaration) null);
        client.emit(new java.util.LinkedHashMap<>());
        client.flush(Duration.ofMillis(300));
        assertTrue(client.stats().get("send_failures") >= 0);
        client.close(Duration.ofMillis(200));
    }

    @Test
    void aFullQueueDiscardsTheOldestAndCountsIt() {
        TelemetryClient client = new TelemetryClient(config(8));
        CountDownLatch blocked = new CountDownLatch(1);
        client.sender((path, batch) -> {
            try {
                blocked.await(2, TimeUnit.SECONDS);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            }
        });
        for (int i = 0; i < 200; i++) {
            client.signal("console.a.b", SignalOptions.payload("n" + i));
        }
        blocked.countDown();
        assertTrue(client.stats().get("dropped") > 0, "the bound must actually bind");
        client.close(Duration.ofMillis(300));
    }

    @Test
    void recordingNeverWaitsOnTheCollector() throws Exception {
        // The collector is unreachable here on purpose: an agent that becomes slow when
        // the collector is slow turns a collector outage into an application outage.
        TelemetryClient client = new TelemetryClient(TelemetryConfig.builder()
                .service("admin")
                .endpoint("http://127.0.0.1:1")
                .enabled(true)
                .flushInterval(Duration.ofMillis(10))
                .timeout(Duration.ofMillis(50))
                .build());
        long started = System.nanoTime();
        for (int i = 0; i < 2000; i++) {
            client.signal("console.a.b", SignalOptions.payload("n" + i));
        }
        long elapsedMillis = (System.nanoTime() - started) / 1_000_000;
        assertTrue(elapsedMillis < 1000, "recording 2000 signals took " + elapsedMillis + " ms");
        client.close(Duration.ofMillis(300));
    }

    @Test
    void aDependencyLinkGoesToItsOwnEndpointImmediately() {
        List<String> paths = new CopyOnWriteArrayList<>();
        List<Map<String, Object>> exported = new CopyOnWriteArrayList<>();
        TelemetryClient client = new TelemetryClient(config(1000));
        client.sender((path, batch) -> {
            paths.add(path);
            exported.addAll(batch);
        });

        RequestContext context =
                new RequestContext("req-9", "10.88.0.31", "10.88.0.31", false);
        context.route("/api/integrations/webhooks/probe");
        TelemetryContext.run(context, () -> client.outbound(
                "http://not-calderwood.example/x",
                EgressDeclaration.from("console.integrations.probe.offlist_host_fetched")
                        .withParam("endpoint")));
        client.flush(Duration.ofSeconds(2));

        assertEquals(List.of("/v1/correlations"), paths);
        Map<String, Object> link = exported.get(0);
        assertEquals("not-calderwood.example", link.get("destination_host"));
        assertEquals("endpoint", link.get("param"));
        assertEquals("/api/integrations/webhooks/probe", link.get("route"));
        assertEquals("10.88.0.31", link.get("peer_ip"));
        assertEquals("req-9", link.get("request_id"));
        client.close(Duration.ofMillis(200));
    }

    @Test
    void hostExtractionSurvivesTheValuesThatArriveInPractice() {
        assertEquals("example.test", EgressDeclaration.hostOf("http://example.test/a?b=c"));
        assertEquals("example.test", EgressDeclaration.hostOf("https://user:pw@example.test:8443/x"));
        assertEquals("example.test", EgressDeclaration.hostOf("example.test"));
        assertEquals("fd00::1", EgressDeclaration.hostOf("http://[fd00::1]:80/x"));
        assertEquals("", EgressDeclaration.hostOf(null));
    }

    @Test
    void anInertClientDoesNothingAtAll() {
        TelemetryClient client = new TelemetryClient(TelemetryConfig.builder().service("").build());
        assertFalse(client.enabled());
        client.signal("console.a.b");
        assertEquals(0L, client.stats().get("enqueued"));
    }

    @Test
    void configurationComesFromTheEnvironment() {
        TelemetryConfig config = TelemetryConfig.fromEnvironment(Map.of(
                "TELEMETRY_SERVICE", "admin",
                "TELEMETRY_ENDPOINT", "http://otel-collector:8900/",
                "TELEMETRY_SYNTHETIC_CIDRS", "10.77.0.0/24, 10.77.1.0/24",
                "TELEMETRY_MAX_PARAMS", "64",
                "TELEMETRY_BATCH_MAX", "9000"));
        assertEquals("admin", config.service());
        assertEquals("http://otel-collector:8900", config.endpoint());
        assertEquals("/v1/traces", config.eventsPath());
        assertEquals("/v1/correlations", config.correlationsPath());
        assertEquals(64, config.maxParams());
        assertEquals(TelemetryConfig.BATCH_CEILING, config.batchMax());
        assertTrue(config.syntheticSources().matches("10.77.1.4"));
    }

    @Test
    void theNameRuleIsTheOneTheCollectorApplies() {
        // Character for character, and asserted rather than assumed. A name this
        // library accepts and the collector refuses produces no error at either end:
        // the record is dropped there, the counter here stays at zero, and the series
        // simply never exists. Three segments minimum, and the first one carries no
        // underscore.
        assertEquals("^[a-z][a-z0-9]*(\\.[a-z0-9_]+){2,}$", TelemetryClient.SIGNAL_NAME.pattern());
        assertTrue(TelemetryClient.SIGNAL_NAME.matcher("console.session.factory_account_signed_in").matches());
        assertTrue(TelemetryClient.SIGNAL_NAME.matcher("console.reporting.ledger.statement_stall").matches());
        assertFalse(TelemetryClient.SIGNAL_NAME.matcher("console.session").matches());
        assertFalse(TelemetryClient.SIGNAL_NAME.matcher("con_sole.a.b").matches());
        assertFalse(TelemetryClient.SIGNAL_NAME.matcher("Console.a.b").matches());
    }

    @Test
    void aDependencyLinkWithARefusedNameIsDroppedRatherThanSentNameless() {
        // A link is the only record a blind outbound effect ever produces. Sending it
        // without a name would let the far end store something nothing can attribute,
        // which reads afterwards exactly like the effect never happening.
        List<Map<String, Object>> exported = new CopyOnWriteArrayList<>();
        TelemetryClient client = new TelemetryClient(config(1000));
        client.sender((path, batch) -> exported.addAll(batch));

        client.outbound("http://somewhere.test/x", "console.probe", "endpoint");
        client.outbound("http://somewhere.test/x", null, "endpoint");
        client.outbound("http://somewhere.test/x", "console.integrations.probe.offlist_host_fetched",
                "endpoint");
        client.flush(Duration.ofSeconds(2));

        assertEquals(1, exported.size());
        assertEquals("console.integrations.probe.offlist_host_fetched", exported.get(0).get("signal"));
        assertEquals(2L, client.stats().get("invalid_signals"));
        client.close(Duration.ofMillis(200));
    }
}
