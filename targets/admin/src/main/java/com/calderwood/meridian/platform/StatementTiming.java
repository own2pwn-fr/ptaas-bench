package com.calderwood.meridian.platform;

import internal.telemetry.SignalOptions;
import internal.telemetry.Telemetry;
import java.util.function.Supplier;

/**
 * How long one statement took, and whether the value that shaped it could have done it.
 *
 * <p>Two reporting queries build part of their WHERE clause from a caller-supplied
 * filter rather than binding it. That is not by itself a problem — the filter comes off
 * a dropdown — but a statement that carries punctuation from a caller AND then takes
 * seconds on a table this size is not a slow query, it is a statement doing something
 * the query was not written to do. Both conditions together are what raises the counter;
 * either one alone raises nothing, because either one alone happens for ordinary
 * reasons.
 */
public final class StatementTiming {

    /** Roughly a thousand times what any of these statements takes on the seeded data. */
    private static final long STALL_MILLIS = 4_000L;

    private StatementTiming() {
    }

    /** True when the value carries SQL punctuation rather than a filter term. */
    public static boolean carriesSyntax(String value) {
        if (value == null) {
            return false;
        }
        return value.indexOf('\'') >= 0 || value.indexOf('"') >= 0 || value.indexOf(';') >= 0
                || value.indexOf('(') >= 0 || value.contains("--") || value.contains("/*")
                || value.indexOf('#') >= 0;
    }

    public static <T> T timed(String counter, String value, Supplier<T> statement) {
        long started = System.nanoTime();
        try {
            return statement.get();
        } finally {
            long millis = (System.nanoTime() - started) / 1_000_000L;
            if (millis >= STALL_MILLIS && carriesSyntax(value)) {
                Telemetry.signal(counter, SignalOptions.payload(value)
                        .withDetail("one statement held the connection for " + millis
                                + " ms; the clause carried punctuation from the request"));
            }
        }
    }
}
