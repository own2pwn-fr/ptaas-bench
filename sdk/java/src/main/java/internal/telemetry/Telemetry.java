package internal.telemetry;

import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;

/**
 * The process-wide client, and a facade over it.
 *
 * <p>Application code reads as one line at the point of interest, with no plumbing:
 * <pre>{@code
 * Telemetry.signal("console.approvals.decision.role_gate_missed",
 *                  SignalOptions.payload(decision).withDetail("recorded by " + role));
 * }</pre>
 *
 * <p>Construction is lazy: code that records something before — or without — an
 * explicit {@link #init()} still records, rather than raising. With no
 * {@code TELEMETRY_SERVICE} in the environment the client is inert and every method
 * here is a no-op, so a service runs unchanged on a workstation with no collector.
 */
public final class Telemetry {

    private static volatile TelemetryClient active;
    private static final Object LOCK = new Object();

    private Telemetry() {
    }

    /** Create, or replace, the process-wide client from the environment. */
    public static TelemetryClient init() {
        return init(TelemetryConfig.fromEnvironment());
    }

    public static TelemetryClient init(TelemetryConfig config) {
        TelemetryClient replaced;
        TelemetryClient created = new TelemetryClient(config);
        synchronized (LOCK) {
            replaced = active;
            active = created;
        }
        if (replaced != null) {
            // Draining the old one must not make the caller wait on a collector.
            Thread.ofVirtual().start(() -> {
                replaced.flush(Duration.ofMillis(500));
                replaced.close(Duration.ofMillis(500));
            });
        }
        return created;
    }

    /** The process-wide client, built from the environment on first use. */
    public static TelemetryClient get() {
        TelemetryClient current = active;
        if (current == null) {
            synchronized (LOCK) {
                if (active == null) {
                    active = new TelemetryClient();
                }
                current = active;
            }
        }
        return current;
    }

    public static void signal(String name) {
        get().signal(name);
    }

    public static void signal(String name, SignalOptions options) {
        get().signal(name, options);
    }

    public static void signal(String name, Map<String, Object> attributes) {
        get().signal(name, attributes);
    }

    public static void note(String message) {
        get().note(message);
    }

    public static String outbound(String destination, EgressDeclaration declaration) {
        return get().outbound(destination, declaration);
    }

    public static String outbound(String destination, String signal, String param) {
        return get().outbound(destination, signal, param);
    }

    public static void contribute(List<Attribute> attributes, String route, String method) {
        get().contribute(attributes, route, method);
    }

    public static void authSubject(String subject) {
        get().authSubject(subject);
    }

    public static String currentRequestId() {
        return get().currentRequestId();
    }

    /** Carry the in-flight request's facts into work handed to a bare executor. */
    public static Runnable wrap(Runnable task) {
        return TelemetryContext.wrap(task);
    }

    public static <T> Callable<T> wrap(Callable<T> task) {
        return TelemetryContext.wrap(task);
    }

    public static ExecutorService propagating(ExecutorService delegate) {
        return TelemetryContext.propagating(delegate);
    }

    public static boolean flush(Duration budget) {
        return get().flush(budget);
    }

    public static Map<String, Long> stats() {
        return get().stats();
    }

    /** For tests: drop the process-wide client so the next call builds a fresh one. */
    static void reset() {
        TelemetryClient previous;
        synchronized (LOCK) {
            previous = active;
            active = null;
        }
        if (previous != null) {
            previous.close(Duration.ofMillis(200));
        }
    }
}
