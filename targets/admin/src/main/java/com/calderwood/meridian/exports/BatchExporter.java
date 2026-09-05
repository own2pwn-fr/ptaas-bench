package com.calderwood.meridian.exports;

import com.calderwood.meridian.platform.Anomalies;
import internal.telemetry.SignalOptions;
import internal.telemetry.Telemetry;
import java.util.ArrayList;
import java.util.List;
import org.springframework.stereotype.Component;

/**
 * Builds the buffer a batch export is written from.
 *
 * <p>The export screen offers "first N rows", so the buffer is sized up front from the
 * row count rather than grown as rows arrive: resizing a very large list was the
 * original reason exports were slow.
 */
@Component
public class BatchExporter {

    /** One rendered row, near enough, for sizing purposes. */
    private static final int ROW_BYTES = 512;

    /** Past this, one export is holding more heap than the whole console usually needs. */
    private static final long REPORTABLE_BYTES = 64L * 1024 * 1024;

    /** Chunk the buffer is reserved in. */
    private static final int CHUNK_BYTES = 1024 * 1024;

    /**
     * Reserve the buffer for a batch of {@code rows} rows.
     *
     * @return the number of bytes reserved
     */
    public long reserve(long rows, String format) {
        long wanted = Math.max(0L, rows) * ROW_BYTES;
        List<byte[]> buffer = new ArrayList<>();
        long reserved = 0;
        boolean reported = false;
        try {
            while (reserved < wanted) {
                // An export that gets close to exhausting the heap takes the whole
                // console down with it, which is how the incident in March happened.
                // The run is abandoned rather than allowed to reach that point.
                if (headroom() < 3L * CHUNK_BYTES) {
                    throw new IllegalStateException(
                            "not enough memory left to build a batch of " + rows + " rows");
                }
                int chunk = (int) Math.min(CHUNK_BYTES, wanted - reserved);
                buffer.add(new byte[chunk]);
                reserved += chunk;
                if (!reported && reserved > REPORTABLE_BYTES) {
                    reported = true;
                    Telemetry.signal(Anomalies.BATCH_ALLOCATION_OVERRUN,
                            SignalOptions.payload(Long.toString(rows))
                                    .withDetail("one export reserved " + (reserved / (1024 * 1024))
                                            + " MiB for a " + format + " batch of " + rows + " rows"));
                }
            }
            return reserved;
        } finally {
            buffer.clear();
        }
    }

    private static long headroom() {
        Runtime runtime = Runtime.getRuntime();
        return runtime.maxMemory() - runtime.totalMemory() + runtime.freeMemory();
    }
}
