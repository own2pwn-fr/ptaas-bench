package internal.telemetry;

import java.util.Collection;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;

/**
 * Where the in-flight {@link RequestContext} lives, and how it travels.
 *
 * <p>A plain {@link ThreadLocal}, not an inheritable one. An inheritable variable is
 * copied when a thread is <em>created</em>, which for a pool means the context of
 * whichever request happened to grow the pool sticks to that worker for the lifetime of
 * the process — every later task then reports the peer and the classification of a
 * request that finished hours ago. Silent, wrong, and hard to see. So propagation is
 * explicit, and the three boundaries that matter are handled:
 *
 * <ul>
 *   <li><strong>A proxied call</strong> (a transactional service method, a cached
 *       method, a security-advised method) runs on the caller's thread. Nothing to do:
 *       the variable is already in scope on the other side of the proxy.</li>
 *   <li><strong>An asynchronous method dispatched by the framework</strong> runs on a
 *       managed executor. {@code internal.telemetry.spring.TelemetryTaskDecorator} is
 *       installed on those executors and carries the context across.</li>
 *   <li><strong>A bare executor</strong> — a hand-built {@link ExecutorService}, or a
 *       parallel stream — copies nothing. Wrap the work with {@link #wrap(Runnable)} or
 *       the whole executor with {@link #propagating(ExecutorService)}.</li>
 * </ul>
 *
 * <p>The wrappers re-enter the same context object rather than a copy, so one wrapped
 * task can safely run on several workers at once, and a counter raised on any of them
 * reports the request that asked for the work.
 */
public final class TelemetryContext {

    private static final ThreadLocal<RequestContext> CURRENT = new ThreadLocal<>();

    private TelemetryContext() {
    }

    /** The context of the request being served, or {@code null} outside one. */
    public static RequestContext current() {
        return CURRENT.get();
    }

    /**
     * Put {@code context} in scope and return a handle that takes it out again.
     *
     * <p>Always used in a try-with-resources or a try/finally. A context left in scope
     * on a pooled thread is worse than no context at all: the next request served by
     * that worker inherits the previous caller's peer address.
     */
    public static Scope open(RequestContext context) {
        RequestContext previous = CURRENT.get();
        CURRENT.set(context);
        return () -> {
            if (previous == null) {
                CURRENT.remove();
            } else {
                CURRENT.set(previous);
            }
        };
    }

    /** Run {@code body} with {@code context} in scope. */
    public static void run(RequestContext context, Runnable body) {
        try (Scope ignored = open(context)) {
            body.run();
        }
    }

    /** Call {@code body} with {@code context} in scope, passing its result back. */
    public static <T> T call(RequestContext context, Supplier<T> body) {
        try (Scope ignored = open(context)) {
            return body.get();
        }
    }

    /** Capture the in-flight context into a task that will run elsewhere. */
    public static Runnable wrap(Runnable task) {
        RequestContext captured = CURRENT.get();
        if (captured == null) {
            return task;
        }
        return () -> {
            try (Scope ignored = open(captured)) {
                task.run();
            }
        };
    }

    /** Capture the in-flight context into a callable that will run elsewhere. */
    public static <T> Callable<T> wrap(Callable<T> task) {
        RequestContext captured = CURRENT.get();
        if (captured == null) {
            return task;
        }
        return () -> {
            try (Scope ignored = open(captured)) {
                return task.call();
            }
        };
    }

    /**
     * Decorate an executor so that everything submitted to it carries the context of
     * whoever submitted it.
     *
     * <p>Capture happens on the submitting thread, at submission time, which is the only
     * moment the caller's context is knowable.
     */
    public static ExecutorService propagating(ExecutorService delegate) {
        return new PropagatingExecutorService(delegate);
    }

    /** Handle returned by {@link #open(RequestContext)}; closing restores what was there. */
    public interface Scope extends AutoCloseable {
        @Override
        void close();
    }

    private record PropagatingExecutorService(ExecutorService delegate) implements ExecutorService {

        @Override
        public void execute(Runnable command) {
            delegate.execute(wrap(command));
        }

        @Override
        public void shutdown() {
            delegate.shutdown();
        }

        @Override
        public List<Runnable> shutdownNow() {
            return delegate.shutdownNow();
        }

        @Override
        public boolean isShutdown() {
            return delegate.isShutdown();
        }

        @Override
        public boolean isTerminated() {
            return delegate.isTerminated();
        }

        @Override
        public boolean awaitTermination(long timeout, TimeUnit unit) throws InterruptedException {
            return delegate.awaitTermination(timeout, unit);
        }

        @Override
        public <T> Future<T> submit(Callable<T> task) {
            return delegate.submit(wrap(task));
        }

        @Override
        public <T> Future<T> submit(Runnable task, T result) {
            return delegate.submit(wrap(task), result);
        }

        @Override
        public Future<?> submit(Runnable task) {
            return delegate.submit(wrap(task));
        }

        @Override
        public <T> List<Future<T>> invokeAll(Collection<? extends Callable<T>> tasks)
                throws InterruptedException {
            return delegate.invokeAll(tasks.stream().map(TelemetryContext::wrap).toList());
        }

        @Override
        public <T> List<Future<T>> invokeAll(Collection<? extends Callable<T>> tasks, long timeout,
                                             TimeUnit unit) throws InterruptedException {
            return delegate.invokeAll(tasks.stream().map(TelemetryContext::wrap).toList(), timeout, unit);
        }

        @Override
        public <T> T invokeAny(Collection<? extends Callable<T>> tasks)
                throws InterruptedException, java.util.concurrent.ExecutionException {
            return delegate.invokeAny(tasks.stream().map(TelemetryContext::wrap).toList());
        }

        @Override
        public <T> T invokeAny(Collection<? extends Callable<T>> tasks, long timeout, TimeUnit unit)
                throws InterruptedException, java.util.concurrent.ExecutionException,
                java.util.concurrent.TimeoutException {
            return delegate.invokeAny(tasks.stream().map(TelemetryContext::wrap).toList(), timeout, unit);
        }
    }
}
