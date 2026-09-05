package internal.telemetry;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Proxy;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * A counter raised deep inside a service class has to inherit the facts of the request
 * that provoked it.
 *
 * <p>This matters more than it looks. If the classification does not travel, work the
 * platform generated for itself is counted as organic traffic — and a record that says
 * a real client did something the platform did to itself is worse than no record.
 *
 * <p>Three boundaries, one per test: a proxied call (same thread), a bare executor
 * (nothing is copied), and a nested call chain several frames deep.
 */
class ContextPropagationTest {

    private final List<Map<String, Object>> exported = new CopyOnWriteArrayList<>();
    private TelemetryClient client;

    /** A service the way an application writes one: no telemetry argument anywhere. */
    interface LedgerService {
        String settle(String window);
    }

    static final class Ledger implements LedgerService {
        @Override
        public String settle(String window) {
            return depth1(window);
        }

        private String depth1(String window) {
            return depth2(window);
        }

        private String depth2(String window) {
            Telemetry.signal("console.reporting.ledger.statement_stall",
                    SignalOptions.payload(window).withDetail("statement held 5 200 ms"));
            return "done";
        }
    }

    @BeforeEach
    void setUp() {
        Telemetry.reset();
        client = Telemetry.init(TelemetryConfig.builder()
                .service("admin")
                .endpoint("http://otel-collector:8900")
                .enabled(true)
                .flushInterval(Duration.ofMillis(10))
                .syntheticCidrs(List.of("10.77.0.0/24"))
                .build());
        client.sender((path, batch) -> exported.addAll(batch));
    }

    @AfterEach
    void tearDown() {
        Telemetry.reset();
    }

    private Map<String, Object> onlySignal() {
        client.flush(Duration.ofSeconds(2));
        List<Map<String, Object>> signals =
                exported.stream().filter(r -> "signal".equals(r.get("type"))).toList();
        assertEquals(1, signals.size(), "expected exactly one signal, got " + exported);
        return signals.get(0);
    }

    private static RequestContext generatedRequest() {
        RequestContext context = new RequestContext("req-1", "10.77.0.9", "10.77.0.9", true);
        context.route("/api/reports/ledger");
        return context;
    }

    @Test
    void aSignalRaisedThreeFramesDownInheritsThePeerAndTheClassification() {
        TelemetryContext.run(generatedRequest(), () -> new Ledger().settle("last-30-days"));

        Map<String, Object> signal = onlySignal();
        assertEquals("console.reporting.ledger.statement_stall", signal.get("signal"));
        assertEquals("10.77.0.9", signal.get("peer_ip"));
        assertEquals(Boolean.TRUE, signal.get("synthetic"));
    }

    @Test
    void aProxyBoundaryChangesNothing() {
        // A transactional, cached or security-advised service is a proxy on the caller's
        // own thread; the context is already in scope on the other side of it. Asserted
        // rather than assumed, because the day it stops being true nothing else fails.
        LedgerService proxied = (LedgerService) Proxy.newProxyInstance(
                getClass().getClassLoader(),
                new Class<?>[]{LedgerService.class},
                (InvocationHandler) (proxy, method, args) -> method.invoke(new Ledger(), args));

        TelemetryContext.run(generatedRequest(), () -> proxied.settle("last-30-days"));

        assertEquals(Boolean.TRUE, onlySignal().get("synthetic"));
    }

    @Test
    void workHandedToABareExecutorLosesEverythingUnlessItIsWrapped() throws Exception {
        ExecutorService pool = Executors.newFixedThreadPool(2);
        try {
            // Unwrapped: nothing travels. This is the failure being defended against,
            // and it is silent — the work succeeds and the record is simply wrong.
            TelemetryContext.run(generatedRequest(), () -> pool.submit(() -> new Ledger().settle("q")));
            pool.awaitTermination(200, TimeUnit.MILLISECONDS);
            client.flush(Duration.ofSeconds(1));
            Map<String, Object> bare = exported.stream()
                    .filter(r -> "signal".equals(r.get("type"))).findFirst().orElseThrow();
            assertNull(bare.get("peer_ip"));
            assertTrue(bare.get("synthetic") == null || Boolean.FALSE.equals(bare.get("synthetic")));

            exported.clear();

            // Wrapped: the facts arrive on the pool thread.
            TelemetryContext.run(generatedRequest(),
                    () -> pool.submit(Telemetry.wrap(() -> new Ledger().settle("q"))));
            pool.awaitTermination(200, TimeUnit.MILLISECONDS);
            Map<String, Object> wrapped = onlySignal();
            assertEquals("10.77.0.9", wrapped.get("peer_ip"));
            assertEquals(Boolean.TRUE, wrapped.get("synthetic"));
        } finally {
            pool.shutdownNow();
        }
    }

    @Test
    void aDecoratedExecutorCarriesTheContextWithoutTheCallSiteKnowing() throws Exception {
        ExecutorService pool = TelemetryContext.propagating(Executors.newSingleThreadExecutor());
        try {
            TelemetryContext.run(generatedRequest(), () -> pool.submit(() -> new Ledger().settle("q")));
            pool.awaitTermination(300, TimeUnit.MILLISECONDS);
            assertEquals(Boolean.TRUE, onlySignal().get("synthetic"));
        } finally {
            pool.shutdownNow();
        }
    }

    @Test
    void theContextIsNeverLeftBehindOnAPooledThread() throws Exception {
        // The reason this is a plain thread-local and not an inheritable one: a stale
        // context on a worker would attach one request's peer to every later task.
        ExecutorService pool = Executors.newSingleThreadExecutor();
        try {
            TelemetryContext.run(generatedRequest(),
                    () -> pool.submit(Telemetry.wrap(() -> new Ledger().settle("q"))));
            pool.submit(() -> assertNull(TelemetryContext.current())).get(2, TimeUnit.SECONDS);
        } finally {
            pool.shutdownNow();
        }
    }

    @Test
    void outsideARequestASignalStillRecords() {
        new Ledger().settle("scheduled");
        Map<String, Object> signal = onlySignal();
        assertNull(signal.get("peer_ip"));
    }
}
