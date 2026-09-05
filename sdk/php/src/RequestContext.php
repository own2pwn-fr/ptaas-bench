<?php

declare(strict_types=1);

namespace Internal\Telemetry;

/**
 * The facts about the request in flight, shared by everything that records anything.
 *
 * WHY THIS IS A PROCESS-WIDE SINGLETON AND NOT A PARAMETER
 * --------------------------------------------------------
 * Other runtimes carry these facts in a task-local or thread-local so that a helper
 * five frames down a call stack can record something without the request object having
 * been threaded through every signature in between. PHP does not need the machinery:
 * one request is one process, and the superglobals it was started with do not change
 * for its whole life.
 *
 * That is turned into a guarantee rather than a convenience. The classification of the
 * traffic, the peer it came from and the request id are derived ONCE, lazily, from
 * `$_SERVER`, and every record made anywhere in the process reads that same object.
 * There is no argument anywhere in this package that lets a caller record something
 * with a different classification from the request it happened inside, so a helper deep
 * inside a handler cannot get it wrong -- not because the author remembered, but
 * because there is no spelling for the mistake.
 */
final class RequestContext
{
    public const UNMATCHED = '<unmatched>';

    /** Route template as the application's own router registered it. */
    public string $route = self::UNMATCHED;

    /** @var list<array{0:string,1:string}> */
    public array $pathParams = [];

    public ?string $authSubject = null;

    /** @var list<array<string,mixed>> */
    public array $extraParams = [];

    /** True once the single request record has been written. */
    public bool $recorded = false;

    /** False outside a served request: a command-line process has no request to record. */
    public bool $served = false;

    public string $method = '';

    public string $path = '';

    public string $queryString = '';

    public string $body = '';

    public string $contentType = '';

    public string $userAgent = '';

    /** @var list<array{0:string,1:string}> */
    public array $headers = [];

    /** @var array<string,string> */
    public array $headerMap = [];

    /** @var list<array{0:string,1:string}> */
    public array $cookies = [];

    /** @var list<array{0:string,1:string}> */
    public array $formFields = [];

    /** @var list<array{0:string,1:string}> */
    public array $fileFields = [];

    public function __construct(
        public readonly string $requestId,
        /**
         * The socket peer, and only that. Empty when what we were handed turned out to
         * be a caller's claim rather than a socket address.
         */
        public readonly string $peerIp,
        /**
         * Whatever address the caller announced about itself, forwarded headers
         * included. Description only: it is never compared against anything.
         */
        public readonly string $clientIp,
        /**
         * True when the socket peer sits in one of the generated-traffic networks.
         * Decided once, here, and inherited by every record made during this request.
         */
        public readonly bool $synthetic,
        public readonly float $startedAt,
    ) {
    }

    /**
     * Build the context for this process from the environment it was started with.
     *
     * @param array<string,mixed> $server normally `$_SERVER`
     * @param list<string>        $syntheticCidrs
     */
    public static function fromServer(array $server, array $syntheticCidrs, int $maxBodyBytes): self
    {
        $headers = Params::headersFromServer($server);
        $headerMap = [];
        foreach ($headers as [$name, $value]) {
            // First occurrence wins, matching how a handler reading one header sees it.
            $headerMap[$name] ??= $value;
        }

        $socketPeer = is_string($server['REMOTE_ADDR'] ?? null) ? (string) $server['REMOTE_ADDR'] : '';
        $peer = Net::peerMatchesForwardedClaim($socketPeer, $headerMap) ? '' : $socketPeer;
        $announced = Net::announcedAddress($headerMap);

        $context = new self(
            requestId: self::newRequestId(),
            peerIp: $peer,
            clientIp: $announced !== '' ? $announced : $socketPeer,
            synthetic: Net::inNetworks($peer, $syntheticCidrs),
            startedAt: microtime(true),
        );

        $context->headers = $headers;
        $context->headerMap = $headerMap;
        $context->served = isset($server['REQUEST_METHOD']);
        if (!$context->served) {
            return $context;
        }

        $context->method = is_string($server['REQUEST_METHOD'] ?? null) ? (string) $server['REQUEST_METHOD'] : 'GET';
        $uri = is_string($server['REQUEST_URI'] ?? null) ? (string) $server['REQUEST_URI'] : '';
        $context->path = $uri === '' ? '' : explode('?', $uri, 2)[0];
        $context->queryString = is_string($server['QUERY_STRING'] ?? null) ? (string) $server['QUERY_STRING'] : '';
        $context->contentType = $headerMap['content-type'] ?? '';
        $context->userAgent = $headerMap['user-agent'] ?? '';
        $context->cookies = isset($headerMap['cookie']) ? Params::parseCookieHeader($headerMap['cookie']) : [];
        $context->body = self::readBody($context->contentType, $maxBodyBytes);

        return $context;
    }

    /**
     * The raw request body, up to the configured ceiling.
     *
     * `php://input` is rewindable in every supported version, so reading it here does
     * not take it away from the handler. It is empty for a multipart body -- the SAPI
     * has already consumed that one -- which is why the multipart case is described
     * from the parsed field names instead.
     */
    private static function readBody(string $contentType, int $maxBodyBytes): string
    {
        if (str_starts_with(Params::baseContentType($contentType), 'multipart/')) {
            return '';
        }
        $stream = @fopen('php://input', 'rb');
        if ($stream === false) {
            return '';
        }
        try {
            $body = @stream_get_contents($stream, $maxBodyBytes);

            return is_string($body) ? $body : '';
        } finally {
            @fclose($stream);
        }
    }

    private static function newRequestId(): string
    {
        try {
            return bin2hex(random_bytes(16));
        } catch (\Throwable) {
            return bin2hex(pack('NNNN', mt_rand(), mt_rand(), mt_rand(), mt_rand()));
        }
    }
}
