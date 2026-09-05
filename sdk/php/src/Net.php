<?php

declare(strict_types=1);

namespace Internal\Telemetry;

/**
 * Address handling.
 *
 * One rule runs through this file: an address a caller announced about itself is
 * description, never a fact. It is recorded, and it is never allowed to take part in a
 * decision. The address the socket reported is the only one that classifies anything.
 */
final class Net
{
    /**
     * Headers through which a caller can announce an address.
     *
     * They are described as ordinary request attributes. They never take part in a
     * classification decision.
     */
    public const FORWARDED_HEADERS = ['x-forwarded-for', 'x-real-ip', 'forwarded', 'true-client-ip', 'client-ip'];

    /**
     * True when the address falls inside one of the given networks.
     *
     * @param list<string> $cidrs
     */
    public static function inNetworks(string $address, array $cidrs): bool
    {
        $address = trim($address);
        if ($address === '' || $cidrs === []) {
            return false;
        }
        $packed = @inet_pton($address);
        if ($packed === false) {
            return false;
        }
        foreach ($cidrs as $cidr) {
            if (self::matches($packed, $cidr)) {
                return true;
            }
        }

        return false;
    }

    private static function matches(string $packed, string $cidr): bool
    {
        $cidr = trim($cidr);
        if ($cidr === '') {
            return false;
        }
        $slash = strrpos($cidr, '/');
        if ($slash === false) {
            $network = @inet_pton($cidr);

            return $network !== false && $network === $packed;
        }
        $network = @inet_pton(substr($cidr, 0, $slash));
        $bits = substr($cidr, $slash + 1);
        if ($network === false || preg_match('/^\d+$/', $bits) !== 1) {
            // A typo in configuration must never break request handling.
            return false;
        }
        $bits = (int) $bits;
        // Mixing families is not an error, just a non-match: a v4 peer is not inside a
        // v6 network however the prefix is written.
        if (strlen($network) !== strlen($packed)) {
            return false;
        }
        $width = strlen($network) * 8;
        if ($bits < 0 || $bits > $width) {
            return false;
        }
        $wholeBytes = intdiv($bits, 8);
        if ($wholeBytes > 0 && substr($packed, 0, $wholeBytes) !== substr($network, 0, $wholeBytes)) {
            return false;
        }
        $remainder = $bits % 8;
        if ($remainder === 0) {
            return true;
        }
        $mask = (0xFF << (8 - $remainder)) & 0xFF;

        return (ord($packed[$wholeBytes]) & $mask) === (ord($network[$wholeBytes]) & $mask);
    }

    /**
     * True when the caller itself announced the address we are about to classify on.
     *
     * Defence in depth against a deployment where something upstream (mod_remoteip, a
     * proxy-header filter) has already replaced the socket address with a header value.
     * The address then is not the socket's, it is the caller's claim, and classifying
     * on it would let any caller decide how its own traffic is counted.
     *
     * @param array<string,string> $headers lower-cased header name => value
     */
    public static function peerMatchesForwardedClaim(string $peer, array $headers): bool
    {
        if ($peer === '') {
            return false;
        }
        foreach (self::FORWARDED_HEADERS as $name) {
            $raw = $headers[$name] ?? '';
            if ($raw === '') {
                continue;
            }
            foreach (explode(',', str_replace(';', ',', $raw)) as $chunk) {
                $candidate = trim(trim($chunk), '"');
                if (str_contains($candidate, '=')) {
                    // Forwarded: for=192.0.2.1;proto=https
                    $candidate = trim(trim(explode('=', $candidate, 2)[1]), '"');
                }
                $candidate = trim($candidate, '[]');
                if ($candidate === $peer) {
                    return true;
                }
                $colon = strrpos($candidate, ':');
                if ($colon !== false && trim(substr($candidate, 0, $colon), '[]') === $peer) {
                    return true;
                }
            }
        }

        return false;
    }

    /**
     * The first address a caller announced about itself, for description only.
     *
     * @param array<string,string> $headers lower-cased header name => value
     */
    public static function announcedAddress(array $headers): string
    {
        foreach (self::FORWARDED_HEADERS as $name) {
            $raw = $headers[$name] ?? '';
            if ($raw === '') {
                continue;
            }
            $first = trim(explode(',', $raw)[0]);
            if ($name === 'forwarded') {
                foreach (explode(';', $first) as $part) {
                    $part = trim($part);
                    if (stripos($part, 'for=') === 0) {
                        return trim(trim(substr($part, 4), '"'), '[]');
                    }
                }
                continue;
            }
            if ($first !== '') {
                return trim($first, '"');
            }
        }

        return '';
    }

    /** Host part of a URL, or the value itself when it is already a host. */
    public static function hostOf(string $destination): string
    {
        $text = trim($destination);
        if ($text === '') {
            return '';
        }
        if (str_contains($text, '//')) {
            $host = parse_url($text, PHP_URL_HOST);
            if (is_string($host) && $host !== '') {
                return strtolower(trim($host, '[]'));
            }
        }
        $head = explode('?', explode('/', $text, 2)[0], 2)[0];
        if (str_starts_with($head, '[') && str_contains($head, ']')) {
            return strtolower(substr($head, 1, strpos($head, ']') - 1));
        }
        $parts = explode('@', $head);
        $head = $parts[count($parts) - 1];

        return strtolower(explode(':', $head, 2)[0]);
    }
}
