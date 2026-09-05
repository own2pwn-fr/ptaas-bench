<?php

declare(strict_types=1);

namespace Internal\Telemetry;

/**
 * A very small HTTP/1.1 POST, written on a stream socket.
 *
 * No client library, for two reasons. The first is dependencies: this package has to
 * install into a service whose extension set we do not control, and cURL is not always
 * one of them. The second is control: everything here has a hard deadline, and a client
 * library that resolves, connects, retries and follows redirects on its own schedule
 * cannot be given one.
 *
 * What is knowingly NOT bounded is name resolution. PHP resolves inside the connect
 * call and the resolver has its own timeout, so the deadlines below start at the point
 * the address is known. That is acceptable where this is used -- the drain loop is not
 * on anybody's request path, and the one caller that is (Telemetry::outbound) is about
 * to make a request of its own to a name it has just been handed anyway.
 */
final class HttpPoster
{
    /**
     * POST a JSON body. Returns true when the far end answered below 500.
     *
     * @param bool $awaitReply false to write and go: the caller has nothing to do with
     *                         the answer, and reading it would only cost time
     */
    public static function post(
        string $endpoint,
        string $path,
        string $body,
        float $timeout,
        bool $awaitReply = true,
    ): bool {
        $parts = parse_url(rtrim($endpoint, '/'));
        if (!is_array($parts) || !isset($parts['host'])) {
            return false;
        }
        $scheme = strtolower((string) ($parts['scheme'] ?? 'http'));
        $transport = $scheme === 'https' ? 'ssl' : 'tcp';
        $port = (int) ($parts['port'] ?? ($scheme === 'https' ? 443 : 80));
        $host = (string) $parts['host'];
        $prefix = rtrim((string) ($parts['path'] ?? ''), '/');
        $target = $prefix . $path;

        $timeout = max(0.01, $timeout);
        $errorNumber = 0;
        $errorText = '';
        $socket = @stream_socket_client(
            $transport . '://' . $host . ':' . $port,
            $errorNumber,
            $errorText,
            $timeout,
            STREAM_CLIENT_CONNECT,
        );
        if (!is_resource($socket)) {
            return false;
        }

        try {
            @stream_set_timeout($socket, (int) $timeout, (int) (fmod($timeout, 1.0) * 1_000_000));
            $hostHeader = $port === ($scheme === 'https' ? 443 : 80) ? $host : $host . ':' . $port;
            $request = 'POST ' . $target . " HTTP/1.1\r\n"
                . 'Host: ' . $hostHeader . "\r\n"
                . "Content-Type: application/json\r\n"
                . 'Content-Length: ' . strlen($body) . "\r\n"
                . "Connection: close\r\n"
                . "\r\n"
                . $body;

            $written = 0;
            $length = strlen($request);
            $deadline = microtime(true) + $timeout;
            while ($written < $length) {
                $sent = @fwrite($socket, substr($request, $written));
                if ($sent === false || $sent === 0) {
                    return false;
                }
                $written += $sent;
                if (microtime(true) > $deadline) {
                    return false;
                }
            }

            if (!$awaitReply) {
                return true;
            }

            $statusLine = (string) @fgets($socket, 256);
            if ($statusLine === '') {
                return false;
            }

            return (bool) preg_match('#^HTTP/1\.[01] [1-4]\d\d#', $statusLine);
        } finally {
            @fclose($socket);
        }
    }
}
