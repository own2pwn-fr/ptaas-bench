package internal.telemetry;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Runtime configuration, read from explicit values first and then from the environment.
 *
 * <p>Services are deployed as containers whose environment carries the {@code TELEMETRY_*}
 * keys, so the environment is the primary source; the builder exists for unit tests and
 * for services that own their own configuration loading.
 *
 * <p>Recognised variables:
 * <ul>
 *   <li>{@code TELEMETRY_SERVICE} — name reported with every record.</li>
 *   <li>{@code TELEMETRY_ENDPOINT} — collector base URL.</li>
 *   <li>{@code TELEMETRY_ENABLED} — master switch.</li>
 *   <li>{@code TELEMETRY_EVENTS_PATH} — default {@code /v1/traces}.</li>
 *   <li>{@code TELEMETRY_CORRELATIONS_PATH} — default {@code /v1/correlations}.</li>
 *   <li>{@code TELEMETRY_SYNTHETIC_CIDRS} — peer networks whose traffic is generated
 *       rather than organic (uptime probes, warm-up jobs, load generators), decided on
 *       the socket peer address and on nothing else.</li>
 *   <li>{@code TELEMETRY_QUEUE_MAX}, {@code TELEMETRY_BATCH_MAX},
 *       {@code TELEMETRY_FLUSH_INTERVAL_MS}, {@code TELEMETRY_TIMEOUT_MS},
 *       {@code TELEMETRY_MAX_BODY_BYTES}, {@code TELEMETRY_MAX_PARAMS}.</li>
 * </ul>
 */
public final class TelemetryConfig {

    /** The collector refuses a larger batch outright, so this is a ceiling, not a default. */
    public static final int BATCH_CEILING = 500;

    private final String service;
    private final String endpoint;
    private final boolean enabled;
    private final String eventsPath;
    private final String correlationsPath;
    private final int queueMax;
    private final int batchMax;
    private final Duration flushInterval;
    private final Duration timeout;
    private final SourceMatcher syntheticSources;
    private final int maxBodyBytes;
    private final int maxParams;

    private TelemetryConfig(Builder b) {
        this.service = b.service == null ? "" : b.service;
        this.endpoint = b.endpoint == null ? "" : stripTrailingSlash(b.endpoint);
        this.enabled = b.enabled != null ? b.enabled : !this.service.isEmpty();
        this.eventsPath = withLeadingSlash(b.eventsPath == null ? "/v1/traces" : b.eventsPath);
        this.correlationsPath =
                withLeadingSlash(b.correlationsPath == null ? "/v1/correlations" : b.correlationsPath);
        this.queueMax = b.queueMax > 0 ? b.queueMax : 10_000;
        this.batchMax = Math.min(b.batchMax > 0 ? b.batchMax : BATCH_CEILING, BATCH_CEILING);
        this.flushInterval = b.flushInterval == null ? Duration.ofMillis(250) : b.flushInterval;
        this.timeout = b.timeout == null ? Duration.ofSeconds(5) : b.timeout;
        this.syntheticSources = b.syntheticSources == null ? SourceMatcher.none() : b.syntheticSources;
        this.maxBodyBytes = b.maxBodyBytes > 0 ? b.maxBodyBytes : 262_144;
        this.maxParams = b.maxParams > 0 ? b.maxParams : 1024;
    }

    public static Builder builder() {
        return new Builder();
    }

    /** Read the environment. Unset and unparseable values fall back to the defaults. */
    public static TelemetryConfig fromEnvironment() {
        return fromEnvironment(System.getenv());
    }

    public static TelemetryConfig fromEnvironment(Map<String, String> env) {
        Builder b = builder()
                .service(env.get("TELEMETRY_SERVICE"))
                .endpoint(env.getOrDefault("TELEMETRY_ENDPOINT", "http://otel-collector:8900"))
                .eventsPath(env.get("TELEMETRY_EVENTS_PATH"))
                .correlationsPath(env.get("TELEMETRY_CORRELATIONS_PATH"))
                .queueMax(intOf(env.get("TELEMETRY_QUEUE_MAX"), 0))
                .batchMax(intOf(env.get("TELEMETRY_BATCH_MAX"), 0))
                .maxBodyBytes(intOf(env.get("TELEMETRY_MAX_BODY_BYTES"), 0))
                .maxParams(intOf(env.get("TELEMETRY_MAX_PARAMS"), 0))
                .syntheticCidrs(listOf(env.get("TELEMETRY_SYNTHETIC_CIDRS")));

        int flush = intOf(env.get("TELEMETRY_FLUSH_INTERVAL_MS"), 0);
        if (flush > 0) {
            b.flushInterval(Duration.ofMillis(flush));
        }
        int timeout = intOf(env.get("TELEMETRY_TIMEOUT_MS"), 0);
        if (timeout > 0) {
            b.timeout(Duration.ofMillis(timeout));
        }
        Boolean on = boolOf(env.get("TELEMETRY_ENABLED"));
        if (on != null) {
            b.enabled(on);
        }
        return b.build();
    }

    public String service() {
        return service;
    }

    public String endpoint() {
        return endpoint;
    }

    /**
     * With no service name nothing is configured, so the client stays inert and every
     * entry point is a no-op. That keeps it silent in local development and in unit
     * tests without any extra wiring.
     */
    public boolean enabled() {
        return enabled && !service.isEmpty();
    }

    public String eventsPath() {
        return eventsPath;
    }

    public String correlationsPath() {
        return correlationsPath;
    }

    public int queueMax() {
        return queueMax;
    }

    public int batchMax() {
        return batchMax;
    }

    public Duration flushInterval() {
        return flushInterval;
    }

    public Duration timeout() {
        return timeout;
    }

    public SourceMatcher syntheticSources() {
        return syntheticSources;
    }

    public int maxBodyBytes() {
        return maxBodyBytes;
    }

    public int maxParams() {
        return maxParams;
    }

    private static String stripTrailingSlash(String url) {
        String value = url.strip();
        return value.endsWith("/") ? value.substring(0, value.length() - 1) : value;
    }

    private static String withLeadingSlash(String path) {
        return path.startsWith("/") ? path : "/" + path;
    }

    private static int intOf(String raw, int fallback) {
        if (raw == null || raw.isBlank()) {
            return fallback;
        }
        try {
            return Integer.parseInt(raw.strip());
        } catch (NumberFormatException malformed) {
            return fallback;
        }
    }

    private static Boolean boolOf(String raw) {
        if (raw == null || raw.isBlank()) {
            return null;
        }
        return switch (raw.strip().toLowerCase(Locale.ROOT)) {
            case "1", "true", "yes", "on" -> Boolean.TRUE;
            case "0", "false", "no", "off" -> Boolean.FALSE;
            default -> null;
        };
    }

    private static List<String> listOf(String raw) {
        List<String> out = new ArrayList<>();
        if (raw == null) {
            return out;
        }
        for (String part : raw.split("[,\\s]+")) {
            if (!part.isBlank()) {
                out.add(part.strip());
            }
        }
        return out;
    }

    /** Mutable builder; every setter tolerates {@code null} as "leave the default". */
    public static final class Builder {
        private String service;
        private String endpoint;
        private Boolean enabled;
        private String eventsPath;
        private String correlationsPath;
        private int queueMax;
        private int batchMax;
        private Duration flushInterval;
        private Duration timeout;
        private SourceMatcher syntheticSources;
        private int maxBodyBytes;
        private int maxParams;

        public Builder service(String value) {
            this.service = value;
            return this;
        }

        public Builder endpoint(String value) {
            this.endpoint = value;
            return this;
        }

        public Builder enabled(boolean value) {
            this.enabled = value;
            return this;
        }

        public Builder eventsPath(String value) {
            this.eventsPath = value;
            return this;
        }

        public Builder correlationsPath(String value) {
            this.correlationsPath = value;
            return this;
        }

        public Builder queueMax(int value) {
            this.queueMax = value;
            return this;
        }

        public Builder batchMax(int value) {
            this.batchMax = value;
            return this;
        }

        public Builder flushInterval(Duration value) {
            this.flushInterval = value;
            return this;
        }

        public Builder timeout(Duration value) {
            this.timeout = value;
            return this;
        }

        public Builder syntheticCidrs(List<String> value) {
            this.syntheticSources = SourceMatcher.of(value == null ? List.of() : value);
            return this;
        }

        public Builder syntheticSources(SourceMatcher value) {
            this.syntheticSources = value;
            return this;
        }

        public Builder maxBodyBytes(int value) {
            this.maxBodyBytes = value;
            return this;
        }

        public Builder maxParams(int value) {
            this.maxParams = value;
            return this;
        }

        public TelemetryConfig build() {
            return new TelemetryConfig(this);
        }
    }
}
