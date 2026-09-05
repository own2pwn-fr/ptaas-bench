package internal.telemetry;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Extra context recorded alongside a signal.
 *
 * <p>Free-form by design, but the three named fields are the ones every dashboard and
 * every post-mortem asks for: what came in, what was observed, and which request it
 * belonged to. Anything else can go in with {@link #with(String, Object)}.
 */
public final class SignalOptions {

    private final Map<String, Object> attributes = new LinkedHashMap<>();
    private Boolean synthetic;

    public static SignalOptions none() {
        return new SignalOptions();
    }

    /** The input that produced the anomaly. */
    public static SignalOptions payload(String value) {
        return new SignalOptions().withPayload(value);
    }

    public SignalOptions withPayload(String value) {
        attributes.put("payload", value);
        return this;
    }

    /** What was actually observed, in a form somebody can act on. */
    public SignalOptions withDetail(String value) {
        attributes.put("detail", value);
        return this;
    }

    /** Correlation id, when the caller is naming one explicitly. */
    public SignalOptions withRequestId(String value) {
        attributes.put("request_id", value);
        return this;
    }

    public SignalOptions with(String key, Object value) {
        attributes.put(key, value);
        return this;
    }

    /**
     * Force the classification of this record.
     *
     * <p>For a probe exercising its own code path from inside the process, where there
     * is no socket peer to decide on.
     */
    public SignalOptions withSynthetic(boolean value) {
        this.synthetic = value;
        return this;
    }

    Map<String, Object> attributes() {
        return attributes;
    }

    Boolean synthetic() {
        return synthetic;
    }
}
