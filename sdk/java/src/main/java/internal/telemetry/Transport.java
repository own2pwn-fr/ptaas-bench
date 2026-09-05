package internal.telemetry;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;

/**
 * The exporter: two bounded in-memory queues drained by two daemon threads.
 *
 * <p>Everything here serves one rule — <strong>the collector must never be observable
 * from a served request</strong>. An agent that adds latency, or that adds latency only
 * when the collector is slow, corrupts the very numbers it exists to measure and turns
 * a collector outage into an application outage. Consequences, all intentional:
 *
 * <ul>
 *   <li>recording is an append under a short lock: no I/O, no name resolution, no lock
 *       held across a syscall, and no exception allowed to escape into the caller;</li>
 *   <li>a daemon thread owns the HTTP client and exports batches on a fixed period, or
 *       as soon as a batch's worth has piled up;</li>
 *   <li>when a queue is full the <em>oldest</em> records are discarded and counted.
 *       Back-pressure is never applied to the application: a lost record costs a line on
 *       a dashboard, a blocked request costs a user;</li>
 *   <li>a collector that is down, hung or answering 500s only ever moves counters.</li>
 * </ul>
 *
 * <p>Dependency links travel on their own queue, their own thread and their own
 * connection. The lookup a link explains happens microseconds later, so a link that
 * waited for the next export tick would arrive after the effect it describes — and a
 * burst of request-controlled destinations must not be able to evict queued records.
 */
final class Transport {

    private final TelemetryConfig config;

    private final Deque<Map<String, Object>> records = new ArrayDeque<>();
    private final Object recordsLock = new Object();
    private final Deque<Map<String, Object>> links = new ArrayDeque<>();
    private final Object linksLock = new Object();

    private final AtomicLong enqueued = new AtomicLong();
    private final AtomicLong sent = new AtomicLong();
    private final AtomicLong dropped = new AtomicLong();
    private final AtomicLong sendFailures = new AtomicLong();
    private final AtomicLong batches = new AtomicLong();
    private final AtomicLong linksSent = new AtomicLong();
    private final AtomicLong linksDropped = new AtomicLong();
    private final AtomicLong linksFailed = new AtomicLong();
    private final AtomicLong inFlight = new AtomicLong();

    private volatile boolean stopping;
    private volatile Thread recordThread;
    private volatile Thread linkThread;
    private volatile HttpClient http;
    private final Object startLock = new Object();

    /** Test seam: when set, batches go here instead of onto a socket. */
    private volatile BatchSender sender;

    Transport(TelemetryConfig config) {
        this.config = config;
    }

    void sender(BatchSender value) {
        this.sender = value;
    }

    // ------------------------------------------------------------------ recording

    void enqueue(Map<String, Object> record) {
        if (!config.enabled() || stopping) {
            return;
        }
        boolean full;
        int size;
        synchronized (recordsLock) {
            if (records.size() >= config.queueMax()) {
                records.pollFirst();
                dropped.incrementAndGet();
            }
            records.addLast(record);
            size = records.size();
        }
        enqueued.incrementAndGet();
        full = size >= config.batchMax();
        startWorkers();
        if (full) {
            wake(recordsLock);
        }
    }

    /**
     * Hand a dependency link straight to the link lane.
     *
     * <p>Immediate still means off the request path: this appends and returns, and
     * another thread does the writing.
     */
    void enqueueLink(Map<String, Object> link) {
        if (!config.enabled() || stopping) {
            return;
        }
        synchronized (linksLock) {
            if (links.size() >= 2048) {
                links.pollFirst();
                linksDropped.incrementAndGet();
            }
            links.addLast(link);
        }
        startWorkers();
        wake(linksLock);
    }

    private static void wake(Object monitor) {
        synchronized (monitor) {
            monitor.notifyAll();
        }
    }

    private void startWorkers() {
        if (recordThread != null && linkThread != null) {
            return;
        }
        synchronized (startLock) {
            if (recordThread == null) {
                recordThread = daemon("telemetry-exporter", this::runRecords);
            }
            if (linkThread == null) {
                linkThread = daemon("telemetry-links", this::runLinks);
            }
        }
    }

    private static Thread daemon(String name, Runnable body) {
        Thread thread = new Thread(body, name);
        thread.setDaemon(true);
        // Below normal: exporting must never compete with request handling for a core.
        thread.setPriority(Thread.NORM_PRIORITY - 1);
        thread.start();
        return thread;
    }

    // ------------------------------------------------------------------ workers

    private void runRecords() {
        while (true) {
            synchronized (recordsLock) {
                if (records.isEmpty() && !stopping) {
                    try {
                        recordsLock.wait(config.flushInterval().toMillis());
                    } catch (InterruptedException interrupted) {
                        Thread.currentThread().interrupt();
                        return;
                    }
                }
            }
            drainRecords();
            if (stopping) {
                drainRecords();
                return;
            }
        }
    }

    private void drainRecords() {
        while (true) {
            List<Map<String, Object>> batch;
            synchronized (recordsLock) {
                if (records.isEmpty()) {
                    return;
                }
                batch = new ArrayList<>(Math.min(config.batchMax(), records.size()));
                while (!records.isEmpty() && batch.size() < config.batchMax()) {
                    batch.add(records.pollFirst());
                }
                inFlight.incrementAndGet();
            }
            try {
                post(config.eventsPath(), Map.of("events", batch), batch.size(), sent, sendFailures);
                batches.incrementAndGet();
            } finally {
                inFlight.decrementAndGet();
            }
        }
    }

    private void runLinks() {
        while (true) {
            synchronized (linksLock) {
                if (links.isEmpty() && !stopping) {
                    try {
                        linksLock.wait(500);
                    } catch (InterruptedException interrupted) {
                        Thread.currentThread().interrupt();
                        return;
                    }
                }
            }
            while (true) {
                Map<String, Object> link;
                synchronized (linksLock) {
                    link = links.pollFirst();
                    if (link != null) {
                        inFlight.incrementAndGet();
                    }
                }
                if (link == null) {
                    break;
                }
                try {
                    post(config.correlationsPath(), link, 1, linksSent, linksFailed);
                } finally {
                    inFlight.decrementAndGet();
                }
            }
            if (stopping) {
                return;
            }
        }
    }

    private void post(String path, Object payload, int count, AtomicLong okCounter, AtomicLong failCounter) {
        BatchSender direct = sender;
        if (direct != null) {
            try {
                @SuppressWarnings("unchecked")
                List<Map<String, Object>> batch = payload instanceof Map<?, ?> map
                        && map.get("events") instanceof List<?> list
                        ? (List<Map<String, Object>>) list
                        : List.of(castRecord(payload));
                direct.send(path, batch);
                okCounter.addAndGet(count);
            } catch (RuntimeException failed) {
                failCounter.incrementAndGet();
            }
            return;
        }
        if (config.endpoint().isEmpty()) {
            failCounter.incrementAndGet();
            return;
        }
        String body;
        try {
            body = Json.write(payload);
        } catch (RuntimeException unserialisable) {
            failCounter.incrementAndGet();
            return;
        }
        // One retry: a collector restarting between two batches is the common transient
        // failure. Beyond that the batch is discarded rather than re-queued, so an
        // unreachable collector can never make memory grow.
        for (int attempt = 0; attempt < 2; attempt++) {
            try {
                HttpRequest request = HttpRequest.newBuilder(URI.create(config.endpoint() + path))
                        .timeout(config.timeout())
                        .header("content-type", "application/json")
                        .POST(HttpRequest.BodyPublishers.ofString(body))
                        .build();
                HttpResponse<Void> response = client().send(request, HttpResponse.BodyHandlers.discarding());
                if (response.statusCode() < 500) {
                    okCounter.addAndGet(count);
                    return;
                }
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                failCounter.incrementAndGet();
                return;
            } catch (Exception unreachable) {
                // Collector down, name gone, timed out: by design a no-op. Rebuild the
                // client so a poisoned connection pool does not persist.
                http = null;
            }
            if (attempt == 0) {
                try {
                    Thread.sleep(20);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
        }
        failCounter.incrementAndGet();
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> castRecord(Object payload) {
        return payload instanceof Map<?, ?> map ? (Map<String, Object>) map : new LinkedHashMap<>();
    }

    private HttpClient client() {
        HttpClient current = http;
        if (current == null) {
            synchronized (startLock) {
                current = http;
                if (current == null) {
                    current = HttpClient.newBuilder()
                            .connectTimeout(Duration.ofSeconds(2))
                            .followRedirects(HttpClient.Redirect.NEVER)
                            .build();
                    http = current;
                }
            }
        }
        return current;
    }

    // ------------------------------------------------------------------ lifecycle

    /**
     * Wait until both queues are empty or the budget elapses.
     *
     * <p>For shutdown hooks and tests only. Nothing on a request path ever calls this.
     */
    boolean flush(Duration budget) {
        if (!config.enabled()) {
            return true;
        }
        startWorkers();
        long deadline = System.nanoTime() + budget.toNanos();
        while (System.nanoTime() < deadline) {
            wake(recordsLock);
            wake(linksLock);
            if (idle()) {
                return true;
            }
            try {
                Thread.sleep(5);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                return idle();
            }
        }
        return idle();
    }

    private boolean idle() {
        synchronized (recordsLock) {
            if (!records.isEmpty()) {
                return false;
            }
        }
        synchronized (linksLock) {
            if (!links.isEmpty()) {
                return false;
            }
        }
        return inFlight.get() == 0;
    }

    void shutdown(Duration budget) {
        flush(budget);
        stopping = true;
        wake(recordsLock);
        wake(linksLock);
        for (Thread thread : new Thread[]{recordThread, linkThread}) {
            if (thread != null) {
                try {
                    thread.join(budget.toMillis());
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
        }
    }

    /** Exporter health. A non-zero {@code dropped} means records were lost. */
    Map<String, Long> stats() {
        Map<String, Long> out = new LinkedHashMap<>();
        out.put("enqueued", enqueued.get());
        out.put("sent", sent.get());
        out.put("dropped", dropped.get());
        out.put("send_failures", sendFailures.get());
        out.put("batches", batches.get());
        out.put("links_sent", linksSent.get());
        out.put("links_dropped", linksDropped.get());
        out.put("links_failed", linksFailed.get());
        synchronized (recordsLock) {
            out.put("queued", (long) records.size());
        }
        synchronized (linksLock) {
            out.put("links_queued", (long) links.size());
        }
        return out;
    }
}
