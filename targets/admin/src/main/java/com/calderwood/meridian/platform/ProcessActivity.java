package com.calderwood.meridian.platform;

import java.util.HashSet;
import java.util.Optional;
import java.util.Set;

/**
 * Did this piece of work start a process?
 *
 * <p>Several places in this service hand a caller's input to something that interprets
 * it — a stylesheet processor, an expression evaluator, a template engine, an object
 * stream. When one of those does something unexpected, the most visible trace is a
 * child process that was not there before, and the operations team's first question
 * after an odd report has always been "did it fork?".
 *
 * <p>This answers exactly that, by sampling the process tree either side of the call.
 * It is a sample and not a hook, so it sees the processes that outlive the call and
 * misses the very shortest. That trade is deliberate: hooking process creation means
 * an agent on the command line of every service, and the sample costs a few
 * microseconds.
 */
public final class ProcessActivity {

    /** A call's result, and the command line of a process it started. */
    public record Outcome<T>(T value, Optional<String> spawned) {

        public boolean started() {
            return spawned.isPresent();
        }
    }

    private ProcessActivity() {
    }

    public static <T> Outcome<T> around(java.util.function.Supplier<T> body) {
        Set<Long> before = descendants();
        T value = body.get();
        return new Outcome<>(value, firstNew(before));
    }

    public static Outcome<Void> around(Runnable body) {
        return around(() -> {
            body.run();
            return null;
        });
    }

    private static Optional<String> firstNew(Set<Long> before) {
        try {
            return ProcessHandle.current().descendants()
                    .filter(handle -> !before.contains(handle.pid()))
                    .findFirst()
                    .map(handle -> handle.info().commandLine().orElse("pid " + handle.pid()));
        } catch (RuntimeException unavailable) {
            return Optional.empty();
        }
    }

    private static Set<Long> descendants() {
        Set<Long> pids = new HashSet<>();
        try {
            ProcessHandle.current().descendants().forEach(handle -> pids.add(handle.pid()));
        } catch (RuntimeException unavailable) {
            // Some platforms refuse to enumerate; the caller simply gets no answer.
        }
        return pids;
    }
}
