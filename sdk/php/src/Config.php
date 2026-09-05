<?php

declare(strict_types=1);

namespace Internal\Telemetry;

/**
 * Runtime configuration, read from explicit arguments then from the environment.
 *
 * Services are deployed as containers whose environment carries the TELEMETRY_* keys,
 * so the environment is the primary source. Explicit arguments exist for unit tests and
 * for services that already own their configuration loading.
 *
 * Every key this class reads starts with TELEMETRY_. That is not cosmetic: an operator
 * reading `env` on a running container should be able to tell at a glance which
 * variables belong to the observability agent and which belong to the application.
 */
final class Config
{
    public const DEFAULT_ENDPOINT = 'http://otel-collector:8900';
    public const DEFAULT_SERVICE = 'unknown-service';

    /** OTLP-style ingest paths on the collector. */
    public const EVENTS_PATH = '/v1/traces';
    public const CORRELATIONS_PATH = '/v1/correlations';

    /** The collector refuses oversized batches, so the drain loop stays well below it. */
    public const BATCH_MAX = 500;

    /** 256 characters is what the record format keeps of any one input value. */
    public const SAMPLE_MAX = 256;

    public function __construct(
        public readonly string $service = self::DEFAULT_SERVICE,
        public readonly string $endpoint = self::DEFAULT_ENDPOINT,
        public readonly bool $enabled = true,
        /**
         * Where records are written on the way out. A local append is the only I/O the
         * request path is allowed to do; see Spool for why.
         */
        public readonly string $spoolPath = '/var/tmp/telemetry/records.jsonl',
        /**
         * Ceiling on the spool. Reached only when nothing is draining it, in which case
         * the most recent records are the ones worth having, and continuing to grow a
         * file on a container's writable layer is how an observability agent takes down
         * the service it was meant to watch.
         */
        public readonly int $spoolMaxBytes = 8_388_608,
        public readonly int $batchMax = self::BATCH_MAX,
        public readonly float $flushInterval = 0.25,
        /**
         * Budget for one dependency-link post. It is the only network call made from a
         * served request, and it is bounded hard for that reason.
         */
        public readonly float $timeout = 0.25,
        /**
         * Networks whose traffic is generated rather than organic: uptime probes,
         * warm-up jobs, release checks. Membership is decided on the socket peer address
         * and on nothing else -- see Telemetry::isSyntheticPeer().
         *
         * @var list<string>
         */
        public readonly array $syntheticCidrs = [],
        /** Request-body bytes read for attribute extraction. */
        public readonly int $maxBodyBytes = 262_144,
        public readonly int $maxParams = 1024,
        public readonly string $eventsPath = self::EVENTS_PATH,
        public readonly string $correlationsPath = self::CORRELATIONS_PATH,
    ) {
    }

    /**
     * Build a configuration from the TELEMETRY_* variables. Non-null overrides win.
     *
     * @param array<string,mixed> $overrides
     */
    public static function fromEnvironment(array $overrides = []): self
    {
        $config = new self(
            service: self::envString('TELEMETRY_SERVICE', self::DEFAULT_SERVICE),
            endpoint: self::envString('TELEMETRY_ENDPOINT', self::DEFAULT_ENDPOINT),
            enabled: self::envBool('TELEMETRY_ENABLED', true),
            spoolPath: self::envString('TELEMETRY_SPOOL_PATH', '/var/tmp/telemetry/records.jsonl'),
            spoolMaxBytes: self::envInt('TELEMETRY_SPOOL_MAX_BYTES', 8_388_608),
            batchMax: min(self::envInt('TELEMETRY_BATCH_MAX', self::BATCH_MAX), self::BATCH_MAX),
            flushInterval: self::envFloat('TELEMETRY_FLUSH_INTERVAL_MS', 250.0) / 1000.0,
            timeout: self::envFloat('TELEMETRY_TIMEOUT_S', 0.25),
            syntheticCidrs: self::envList('TELEMETRY_SYNTHETIC_CIDRS'),
            maxBodyBytes: self::envInt('TELEMETRY_MAX_BODY_BYTES', 262_144),
            maxParams: self::envInt('TELEMETRY_MAX_PARAMS', 1024),
            eventsPath: self::envString('TELEMETRY_EVENTS_PATH', self::EVENTS_PATH),
            correlationsPath: self::envString('TELEMETRY_CORRELATIONS_PATH', self::CORRELATIONS_PATH),
        );

        return $overrides === [] ? $config : $config->with($overrides);
    }

    /**
     * A copy with some fields replaced. Null values are ignored, so a caller can pass a
     * whole option array through without filtering it first.
     *
     * @param array<string,mixed> $overrides
     */
    public function with(array $overrides): self
    {
        $pick = static fn (string $key, mixed $current): mixed
            => array_key_exists($key, $overrides) && $overrides[$key] !== null ? $overrides[$key] : $current;

        return new self(
            service: (string) $pick('service', $this->service),
            endpoint: (string) $pick('endpoint', $this->endpoint),
            enabled: (bool) $pick('enabled', $this->enabled),
            spoolPath: (string) $pick('spoolPath', $this->spoolPath),
            spoolMaxBytes: (int) $pick('spoolMaxBytes', $this->spoolMaxBytes),
            batchMax: (int) $pick('batchMax', $this->batchMax),
            flushInterval: (float) $pick('flushInterval', $this->flushInterval),
            timeout: (float) $pick('timeout', $this->timeout),
            syntheticCidrs: (array) $pick('syntheticCidrs', $this->syntheticCidrs),
            maxBodyBytes: (int) $pick('maxBodyBytes', $this->maxBodyBytes),
            maxParams: (int) $pick('maxParams', $this->maxParams),
            eventsPath: (string) $pick('eventsPath', $this->eventsPath),
            correlationsPath: (string) $pick('correlationsPath', $this->correlationsPath),
        );
    }

    private static function raw(string $name): ?string
    {
        $value = getenv($name);
        if ($value === false) {
            $value = $_SERVER[$name] ?? null;
        }

        return is_string($value) ? $value : null;
    }

    private static function envString(string $name, string $default): string
    {
        $value = self::raw($name);

        return $value === null || $value === '' ? $default : $value;
    }

    private static function envBool(string $name, bool $default): bool
    {
        $value = self::raw($name);
        if ($value === null) {
            return $default;
        }
        $lowered = strtolower(trim($value));
        if (in_array($lowered, ['1', 'true', 'yes', 'on'], true)) {
            return true;
        }
        if (in_array($lowered, ['0', 'false', 'no', 'off'], true)) {
            return false;
        }

        return $default;
    }

    private static function envInt(string $name, int $default): int
    {
        $value = self::raw($name);

        return $value !== null && preg_match('/^-?\d+$/', trim($value)) === 1 ? (int) trim($value) : $default;
    }

    private static function envFloat(string $name, float $default): float
    {
        $value = self::raw($name);

        return $value !== null && is_numeric(trim($value)) ? (float) trim($value) : $default;
    }

    /** @return list<string> */
    private static function envList(string $name): array
    {
        $value = self::raw($name);
        if ($value === null || trim($value) === '') {
            return [];
        }

        return array_values(array_filter(array_map('trim', explode(',', $value)), static fn (string $s): bool => $s !== ''));
    }
}
