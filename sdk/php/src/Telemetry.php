<?php

declare(strict_types=1);

namespace Internal\Telemetry;

/**
 * Internal telemetry agent: request records, application signals, dependency links.
 *
 * The agent turns every served request into exactly one record -- the route template
 * the router matched, the status, and every input the handler could have observed --
 * and lets application code raise named signals when it notices something worth
 * counting.
 *
 * Wiring a service, in its front controller or in an auto-prepended bootstrap:
 *
 *     use Internal\Telemetry\Telemetry;
 *
 *     $telemetry = Telemetry::init();     // TELEMETRY_SERVICE / TELEMETRY_ENDPOINT
 *     $telemetry->observe();              // capture the request, record it at shutdown
 *
 * The router says which template it matched, once it knows:
 *
 *     $telemetry->route('/orders/{id}', ['id' => $id]);
 *
 * Application code raises a signal where it notices an effect worth counting:
 *
 *     $telemetry->signal('orders.export.formula_cell',
 *         ['payload' => $cell, 'detail' => 'cell opens with a calculation prefix']);
 *
 * And declares an outbound dependency whose destination came from the request, so the
 * egress the network sees can be tied back to the request that caused it:
 *
 *     $telemetry->outbound($url, signal: 'imports.feed.fetch_external', param: 'source');
 *
 * Four properties this agent must not lose.
 *
 * 1. NO ADDED LATENCY, NO FAILURE PROPAGATION. Recording is an append to a local file
 *    (see Spool for the reasoning, and for the alternative that was rejected). A
 *    collector that is down, slow or absent changes nothing observable in the service,
 *    including its response times.
 * 2. NOTHING ON THE RESPONSE PATH. No response header, no extra route, no marker in an
 *    error body, no log line on the happy path. A capture of this service looks the
 *    same whether the agent is loaded or not.
 * 3. THE PEER IS WHAT THE SOCKET SAID. Every record carries `peer_ip`, which is
 *    `$_SERVER['REMOTE_ADDR']` and nothing else, and it is the only address anything
 *    downstream classifies traffic on. `client_ip` carries what the caller announced
 *    about itself, as description.
 * 4. ROUTE TEMPLATES, NOT URLS. `/orders/{id}`, never `/orders/4192`, and
 *    `<unmatched>` when the router matched nothing, with the concrete path alongside.
 *
 * Signal names are metric names. They are validated against the shape the collector
 * accepts, and a name that does not match is counted and dropped rather than raised: a
 * service must never fail because of a typo here, and a malformed name would create a
 * series nothing is watching anyway.
 *
 * Environment: TELEMETRY_SERVICE, TELEMETRY_ENDPOINT, TELEMETRY_ENABLED,
 * TELEMETRY_SYNTHETIC_CIDRS, TELEMETRY_SPOOL_PATH, TELEMETRY_SPOOL_MAX_BYTES,
 * TELEMETRY_EVENTS_PATH, TELEMETRY_CORRELATIONS_PATH, TELEMETRY_BATCH_MAX,
 * TELEMETRY_FLUSH_INTERVAL_MS, TELEMETRY_TIMEOUT_S, TELEMETRY_MAX_BODY_BYTES,
 * TELEMETRY_MAX_PARAMS.
 */
final class Telemetry
{
    /**
     * Metric-shaped, dotted, lower case, at least three segments. Kept stricter on
     * purpose than what a name "could" be, so that anything this agent emits is
     * something the collector will accept rather than count as malformed.
     */
    public const SIGNAL_NAME = '/^[a-z][a-z0-9]*(\.[a-z0-9_]+){2,}$/';

    /** What one attribute string is worth keeping. The far end clips at the same width. */
    private const ATTRIBUTE_MAX = 1024;

    private static ?self $active = null;

    private ?RequestContext $context = null;

    private bool $shutdownRegistered = false;

    private readonly Spool $spool;

    /** @var array<string,int> */
    private array $counters = [
        'signals' => 0,
        'notes' => 0,
        'requests' => 0,
        'links_sent' => 0,
        'links_failed' => 0,
        'invalid_names' => 0,
    ];

    public function __construct(private readonly Config $config)
    {
        $this->spool = new Spool($config);
    }

    /** Create, or replace, the process-wide agent. */
    public static function init(?Config $config = null): self
    {
        self::$active = new self($config ?? Config::fromEnvironment());

        return self::$active;
    }

    /**
     * The process-wide agent, built from the environment on first use.
     *
     * Lazy construction matters: code that raises a signal before, or without, an
     * explicit init() must still record rather than fail.
     */
    public static function instance(): self
    {
        return self::$active ??= new self(Config::fromEnvironment());
    }

    /** Drop the process-wide agent. Tests only. */
    public static function reset(): void
    {
        self::$active = null;
    }

    public function config(): Config
    {
        return $this->config;
    }

    // ---------------------------------------------------------------- the request

    /**
     * The context for this process, derived once from the environment it started with.
     *
     * Everything that records anything goes through here, which is what makes the
     * classification of a signal raised deep inside a handler the same as the
     * classification of the request it happened inside -- structurally, with no way to
     * pass a different one.
     */
    public function context(): RequestContext
    {
        return $this->context ??= RequestContext::fromServer(
            $_SERVER,
            $this->config->syntheticCidrs,
            $this->config->maxBodyBytes,
        );
    }

    /**
     * Capture the request and arrange for its single record to be written at teardown.
     *
     * Safe to call more than once: a second call is a no-op, so an application that
     * bootstraps through both an auto-prepended file and its own front controller
     * still produces exactly one record.
     */
    public function observe(): void
    {
        try {
            $context = $this->context();
            if (!$context->served || $context->recorded) {
                return;
            }
            $context->formFields = $this->formFields();
            $context->fileFields = $this->fileFields();
            if (!$this->shutdownRegistered) {
                $this->shutdownRegistered = true;
                register_shutdown_function(function (): void {
                    $this->recordRequest();
                });
            }
        } catch (\Throwable) {
            // An agent that can throw into a request handler is not an agent.
        }
    }

    /**
     * Declare the template the router matched, and the values it bound.
     *
     * Called by the router at match time. Dashboards group by template, so a concrete
     * URL here would put every identifier in its own series and make the grouping
     * useless.
     *
     * @param array<string,scalar|null> $pathParams
     */
    public function route(string $template, array $pathParams = []): void
    {
        try {
            $context = $this->context();
            $context->route = $template === '' ? RequestContext::UNMATCHED : $template;
            $bound = [];
            foreach ($pathParams as $name => $value) {
                $bound[] = [(string) $name, $value === null ? '' : (string) $value];
            }
            $context->pathParams = $bound;
        } catch (\Throwable) {
        }
    }

    /**
     * Declare the authenticated principal of the request in flight.
     *
     * Only the application knows who its session belongs to, and a record without a
     * subject cannot answer "who was served this?" after the fact.
     */
    public function authSubject(?string $subject): void
    {
        try {
            $this->context()->authSubject = $subject;
        } catch (\Throwable) {
        }
    }

    /**
     * Contribute inputs the agent could not see for itself.
     *
     * @param iterable<array{0:string,1:string}> $pairs
     */
    public function addParams(iterable $pairs, string $location): void
    {
        try {
            $context = $this->context();
            foreach ($pairs as $pair) {
                $context->extraParams[] = Params::describe(
                    (string) $pair[0],
                    $location,
                    (string) $pair[1],
                    Config::SAMPLE_MAX,
                );
            }
        } catch (\Throwable) {
        }
    }

    public function requestId(): ?string
    {
        try {
            return $this->context()->requestId;
        } catch (\Throwable) {
            return null;
        }
    }

    // ---------------------------------------------------------------- recording

    /**
     * Record a named application signal with free-form attributes.
     *
     * Signals are the counters an application raises for itself: a query plan that came
     * back with an unexpected row shape, a template that resolved outside the directory
     * it was meant to, a subject id that did not match the row it was handed. They are
     * named like metrics and carry whatever context makes the occurrence explicable
     * later.
     *
     * Raise one on an observed EFFECT, not on the shape of an input. A counter that
     * moves whenever a request merely looks unusual is noise nobody can act on, and it
     * will be switched off by the first person who tries.
     *
     * There is no way to pass a classification here, on purpose. The signal inherits
     * the classification of the request it happened inside, whatever depth of call
     * stack it was raised from.
     *
     * @param array<string,mixed> $attributes `payload`, `detail` and `request_id` are
     *                                        the keys anything downstream reads
     */
    public function signal(string $name, array $attributes = []): void
    {
        try {
            if (!$this->config->enabled) {
                return;
            }
            if (preg_match(self::SIGNAL_NAME, $name) !== 1) {
                $this->counters['invalid_names']++;

                return;
            }
            $record = $this->base('signal');
            $record['signal'] = $name;
            $properties = [];
            foreach ($attributes as $key => $value) {
                $properties[(string) $key] = Params::clip(self::text($value), self::ATTRIBUTE_MAX);
            }
            $requestId = $this->requestId();
            if (!isset($properties['request_id']) && $requestId !== null) {
                $properties['request_id'] = $requestId;
            }
            if ($properties !== []) {
                $record['attributes'] = $properties;
            }
            $this->spool->append($record);
            $this->counters['signals']++;
        } catch (\Throwable) {
        }
    }

    /** Free-form breadcrumb, kept beside the records of the same period. */
    public function note(string $message): void
    {
        try {
            if (!$this->config->enabled) {
                return;
            }
            $record = $this->base('note');
            $record['message'] = Params::clip($message, 4096);
            $this->spool->append($record);
            $this->counters['notes']++;
        } catch (\Throwable) {
        }
    }

    /**
     * Declare an outbound dependency call whose destination came from the request.
     *
     * Call it immediately BEFORE the fetch:
     *
     *     $telemetry->outbound($url, signal: 'imports.feed.fetch_external', param: 'source');
     *
     * A request-controlled destination means the resulting egress -- a name lookup, a
     * connection, a hit on some third party -- appears in the network's own logs with
     * nothing tying it back to the request that caused it. Declaring the pairing
     * beforehand is what lets the two sides be joined afterwards.
     *
     * This is the one thing in this package that goes over the network from a served
     * request, and the exception is chosen rather than accidental. The lookup it
     * describes follows within microseconds; a record that waited for the next drain
     * would arrive after the effect it explains, and a join that has to guess is not a
     * join. The call is bounded by TELEMETRY_TIMEOUT_S (250 ms by default), does not
     * read the answer, and falls back to the spool if the far end is not there -- so
     * the evidence survives even when the immediate path does not.
     */
    public function outbound(string $destination, ?string $signal = null, ?string $param = null, ?string $route = null): void
    {
        try {
            if (!$this->config->enabled) {
                return;
            }
            $context = $this->context();
            $record = [
                'app' => $this->config->service,
                'ts' => microtime(true),
                'synthetic' => $context->synthetic,
                'peer_ip' => $context->peerIp,
                'destination_host' => Net::hostOf($destination),
            ];
            if ($context->clientIp !== '') {
                $record['client_ip'] = $context->clientIp;
            }
            if ($signal !== null && $signal !== '') {
                if (preg_match(self::SIGNAL_NAME, $signal) === 1) {
                    $record['signal'] = $signal;
                } else {
                    $this->counters['invalid_names']++;
                }
            }
            if ($param !== null && $param !== '') {
                $record['param'] = $param;
            }
            $resolved = $route ?? $context->route;
            if ($resolved !== '') {
                $record['route'] = $resolved;
            }
            $record['request_id'] = $context->requestId;

            $body = json_encode($record, JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE);
            $delivered = false;
            if ($body !== false) {
                $delivered = HttpPoster::post(
                    $this->config->endpoint,
                    $this->config->correlationsPath,
                    $body,
                    $this->config->timeout,
                    false,
                );
            }
            if ($delivered) {
                $this->counters['links_sent']++;

                return;
            }
            $this->counters['links_failed']++;
            // The immediate path did not work. The record still belongs in the stream,
            // so it goes out the ordinary way and the join is done later instead of
            // live.
            $record['type'] = 'correlation';
            $this->spool->append($record);
        } catch (\Throwable) {
        }
    }

    /**
     * Write the one record for this request. Registered as a shutdown function by
     * observe(), and idempotent so that an explicit call is harmless.
     */
    public function recordRequest(?int $status = null): void
    {
        try {
            if (!$this->config->enabled) {
                return;
            }
            $context = $this->context();
            if (!$context->served || $context->recorded) {
                return;
            }
            $context->recorded = true;

            $bag = new ParamBag($this->config->maxParams, Config::SAMPLE_MAX);
            $bag->addMany(Params::parseQuery($context->queryString), 'query');
            $bag->addMany($context->pathParams, 'path');
            foreach ($context->headers as [$name, $value]) {
                if ($name === 'cookie') {
                    continue;
                }
                if (Params::isDescribedHeader($name)) {
                    $bag->add($name, 'header', $value);
                }
            }
            $bag->addMany($context->cookies, 'cookie');
            $this->describeBody($bag, $context);
            $bag->extend($context->extraParams);

            $record = $this->base('http_request');
            $record['method'] = $context->method;
            $record['route'] = $context->route;
            $record['path'] = $context->path;
            $code = $status ?? $this->responseStatus();
            if ($code !== null) {
                $record['status'] = $code;
            }
            $record['auth_subject'] = $context->authSubject;
            $record['client_ip'] = $context->clientIp;
            $record['user_agent'] = $context->userAgent;
            $record['params'] = $bag->entries();
            $record['request_id'] = $context->requestId;

            $this->spool->append($record);
            $this->counters['requests']++;
        } catch (\Throwable) {
        }
    }

    // ------------------------------------------------------------------ helpers

    /**
     * True when the socket peer sits in one of the configured generated-traffic
     * networks.
     *
     * The argument must be the SOCKET PEER address and nothing else. Never a forwarded
     * header, never a helper that folds one in: any caller can send
     * `X-Forwarded-For`, so a decision taken on it is a decision taken by the caller.
     * Forwarded values are still described as ordinary request attributes; they are
     * just never allowed to classify the traffic they came with.
     */
    public function isSyntheticPeer(?string $peerIp): bool
    {
        return $peerIp !== null && Net::inNetworks($peerIp, $this->config->syntheticCidrs);
    }

    /** @return array<string,int> */
    public function stats(): array
    {
        return $this->counters + $this->spool->stats();
    }

    /** The spool, for the drain process and for tests. */
    public function spool(): Spool
    {
        return $this->spool;
    }

    /** @return array<string,mixed> */
    private function base(string $type): array
    {
        $context = $this->context();

        return [
            'type' => $type,
            'app' => $this->config->service,
            'ts' => microtime(true),
            // The address the socket reported, and only that. By the time a record
            // reaches the collector the peer IT sees is this container, so here is the
            // only place the real one can be observed.
            'peer_ip' => $context->peerIp,
            'synthetic' => $context->synthetic,
        ];
    }

    private function responseStatus(): ?int
    {
        if (!function_exists('http_response_code')) {
            return null;
        }
        $code = http_response_code();

        return is_int($code) ? $code : null;
    }

    /**
     * Describe the request body by content type, sniffing JSON when the type is absent.
     */
    private function describeBody(ParamBag $bag, RequestContext $context): void
    {
        $base = Params::baseContentType($context->contentType);

        if (str_starts_with($base, 'multipart/')) {
            // The SAPI consumed the raw body, so the parsed field names are all there
            // is. Duplicate field names are lost with it; that is the SAPI's doing and
            // there is nothing left on this side of it to recover them from.
            $bag->addMany($context->formFields, 'multipart');
            $bag->addMany($context->fileFields, 'multipart');

            return;
        }

        if ($context->body === '') {
            return;
        }

        if ($base === 'application/x-www-form-urlencoded') {
            $bag->addMany(Params::parseQuery($context->body), 'body');

            return;
        }

        $looksLikeJson = $base === 'application/json'
            || str_ends_with($base, '+json')
            || ($base === '' && in_array(substr(ltrim($context->body), 0, 1), ['{', '['], true));
        if ($looksLikeJson) {
            $decoded = json_decode($context->body, true);
            if (json_last_error() === JSON_ERROR_NONE) {
                $bag->addMany(Params::flattenJson($decoded), 'json');

                return;
            }
            $bag->add('body', 'raw', $context->body);

            return;
        }

        $bag->add('body', 'raw', $context->body);
    }

    /**
     * Multipart text fields, from the parsed view. Nested names are flattened to the
     * dotted paths the rest of the record uses.
     *
     * @return list<array{0:string,1:string}>
     */
    private function formFields(): array
    {
        $base = Params::baseContentType($this->context()->contentType);
        if (!str_starts_with($base, 'multipart/')) {
            return [];
        }
        $out = [];
        foreach ($_POST as $name => $value) {
            foreach (Params::flattenJson($value, (string) $name) as $pair) {
                $out[] = $pair;
            }
        }

        return $out;
    }

    /**
     * Uploaded parts: the field name, its declared file name, and a bounded prefix of
     * the bytes so that two uploads of the same content group together.
     *
     * @return list<array{0:string,1:string}>
     */
    private function fileFields(): array
    {
        $out = [];
        foreach ($_FILES as $field => $entry) {
            if (!is_array($entry)) {
                continue;
            }
            $names = $entry['name'] ?? '';
            $paths = $entry['tmp_name'] ?? '';
            $nameList = is_array($names) ? array_values($names) : [$names];
            $pathList = is_array($paths) ? array_values($paths) : [$paths];
            foreach ($nameList as $index => $fileName) {
                $label = is_array($names) ? $field . '.' . $index : (string) $field;
                $out[] = [$label . '.filename', is_string($fileName) ? $fileName : ''];
                $tmp = $pathList[$index] ?? '';
                if (is_string($tmp) && $tmp !== '' && is_readable($tmp)) {
                    $content = @file_get_contents($tmp, false, null, 0, $this->config->maxBodyBytes);
                    $out[] = [$label, is_string($content) ? $content : ''];
                }
            }
        }

        return $out;
    }

    private static function text(mixed $value): string
    {
        if (is_string($value)) {
            return $value;
        }
        if ($value === null) {
            return '';
        }
        if (is_scalar($value)) {
            return (string) $value;
        }
        $encoded = json_encode($value, JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE);

        return $encoded === false ? '' : $encoded;
    }
}
