package com.calderwood.meridian.workspace;

import com.calderwood.meridian.platform.ProcessActivity;
import internal.telemetry.SignalOptions;
import internal.telemetry.Telemetry;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.ObjectInputStream;
import java.io.ObjectOutputStream;
import java.util.Base64;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Reads and writes a saved layout.
 *
 * <p>Restoring one is supposed to rebuild fields and, on layouts that carry one, run the
 * warm-up step. Anything else that happens while a layout is being read is worth
 * knowing about, so the read is watched: the warm-up says when it ran, and a process
 * that appears during the read says the same thing about a layout that has no warm-up
 * declared at all.
 */
public final class LayoutCodec {

    /** Which counter the read in progress belongs to. */
    private record Restore(String counter, String param, AtomicBoolean raised) {
    }

    private static final ThreadLocal<Restore> CURRENT = new ThreadLocal<>();

    private LayoutCodec() {
    }

    public static String write(LayoutState state) {
        try (ByteArrayOutputStream bytes = new ByteArrayOutputStream()) {
            try (ObjectOutputStream out = new ObjectOutputStream(bytes)) {
                out.writeObject(state);
            }
            return Base64.getEncoder().encodeToString(bytes.toByteArray());
        } catch (Exception unwritable) {
            return "";
        }
    }

    /**
     * Restore a layout from what a caller presented.
     *
     * @param encoded the saved form, base64 of the object stream
     * @param counter the counter this read belongs to
     * @param param   the input the layout arrived in
     */
    public static LayoutState read(String encoded, String counter, String param) {
        if (encoded == null || encoded.isBlank()) {
            return null;
        }
        byte[] blob;
        try {
            blob = Base64.getDecoder().decode(encoded.trim());
        } catch (IllegalArgumentException notBase64) {
            return null;
        }
        AtomicBoolean raised = new AtomicBoolean();
        CURRENT.set(new Restore(counter, param, raised));
        try {
            ProcessActivity.Outcome<LayoutState> outcome = ProcessActivity.around(() -> {
                try (ObjectInputStream in = new ObjectInputStream(new ByteArrayInputStream(blob))) {
                    Object read = in.readObject();
                    return read instanceof LayoutState state ? state : null;
                } catch (Exception unreadable) {
                    return null;
                }
            });
            if (outcome.started() && raised.compareAndSet(false, true)) {
                Telemetry.signal(counter, SignalOptions.payload(clip(encoded))
                        .withDetail("reading a saved layout started a process: "
                                + outcome.spawned().orElse("")));
            }
            return outcome.value();
        } finally {
            CURRENT.remove();
        }
    }

    /** Called by the warm-up step once its body has run. */
    static void hookRan(String panelId, String command) {
        Restore restore = CURRENT.get();
        if (restore == null || !restore.raised().compareAndSet(false, true)) {
            return;
        }
        Telemetry.signal(restore.counter(), SignalOptions.payload(clip(command))
                .withDetail("the warm-up step on panel " + panelId
                        + " ran while a saved layout was being read"));
    }

    private static String clip(String value) {
        if (value == null) {
            return "";
        }
        return value.length() <= 400 ? value : value.substring(0, 400);
    }
}
