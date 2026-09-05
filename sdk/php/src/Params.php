<?php

declare(strict_types=1);

namespace Internal\Telemetry;

/**
 * Turn a raw request into the `params` array of a record.
 *
 * A latency or error record is only actionable if it says which inputs the handler
 * could see, so every location is described: query string, form body, JSON body,
 * multipart field names, path variables, cookies, and the headers a handler may key
 * behaviour off.
 *
 * Everything is parsed from the RAW bytes rather than from PHP's parsed view. That is
 * not fussiness. `$_GET`, `$_POST` and `$_COOKIE` are lossy in three ways that matter
 * here: a repeated name keeps only its last value, a dot or a space in a name is
 * rewritten to an underscore, and a name with `[]` in it becomes an array. The
 * requests worth looking at are exactly the ones where those details differ from what
 * the handler thought it received.
 *
 * Values are described as (sha256, length, 256-character sample) rather than kept
 * verbatim: the hash groups requests that carried the same value, and separates a
 * default value from an unusual one, without carrying user data down the pipeline.
 */
final class Params
{
    /**
     * Headers worth describing: the ones a handler, a proxy or a cache may key
     * behaviour off. Everything `x-*` is included because custom headers are where
     * per-tenant and feature-toggle routing lives.
     */
    private const DESCRIBED_HEADERS = [
        'host' => true,
        'referer' => true,
        'user-agent' => true,
        'origin' => true,
        'content-type' => true,
        'accept-language' => true,
        'authorization' => true,
        'forwarded' => true,
        'true-client-ip' => true,
    ];

    /** Guard on nesting. Documents a few megabytes deep do arrive, and this runs on the request path. */
    private const JSON_DEPTH_MAX = 16;

    public static function isDescribedHeader(string $name): bool
    {
        $lowered = strtolower($name);

        return isset(self::DESCRIBED_HEADERS[$lowered]) || str_starts_with($lowered, 'x-');
    }

    public static function sha256(string $value): string
    {
        return hash('sha256', $value);
    }

    /**
     * Describe one input.
     *
     * `value_len` counts bytes. PHP strings are byte strings and an upload part is
     * binary, so bytes is the only measure that is meaningful for all of them.
     *
     * @return array{name:string,in:string,value_sha256:string,value_len:int,sample:string}
     */
    public static function describe(string $name, string $location, string $value, int $sampleMax = Config::SAMPLE_MAX): array
    {
        return [
            'name' => $name,
            'in' => $location,
            'value_sha256' => self::sha256($value),
            'value_len' => strlen($value),
            'sample' => self::clip($value, $sampleMax),
        ];
    }

    /**
     * Cut a value to a sample without splitting a UTF-8 sequence down the middle.
     *
     * A split sequence is not merely ugly: it makes the record unencodable, and losing
     * a whole record to one ragged byte is the expensive failure here.
     */
    public static function clip(string $value, int $max = Config::SAMPLE_MAX): string
    {
        if ($max <= 0 || strlen($value) <= $max) {
            return $value;
        }
        $cut = substr($value, 0, $max);
        // Walk back over at most three continuation bytes, then drop the lead byte if
        // the sequence it opened did not fit.
        $back = 0;
        while ($back < 3 && $back < strlen($cut)) {
            $byte = ord($cut[strlen($cut) - 1 - $back]);
            if ($byte >= 0x80 && $byte <= 0xBF) {
                $back++;
                continue;
            }
            if ($byte >= 0xC0) {
                $needed = match (true) {
                    $byte >= 0xF0 => 4,
                    $byte >= 0xE0 => 3,
                    default => 2,
                };
                if ($needed > $back + 1) {
                    return substr($cut, 0, strlen($cut) - 1 - $back);
                }
            }
            break;
        }

        return $cut;
    }

    /**
     * Split a urlencoded string into ordered (name, value) pairs, duplicates kept.
     *
     * `parse_str()` cannot be used: it collapses `a=1&a=2` to a single entry and
     * rewrites names, which is precisely the information this record exists to carry.
     *
     * @return list<array{0:string,1:string}>
     */
    public static function parseQuery(string $raw): array
    {
        $out = [];
        if ($raw === '') {
            return $out;
        }
        foreach (explode('&', $raw) as $chunk) {
            if ($chunk === '') {
                continue;
            }
            $position = strpos($chunk, '=');
            if ($position === false) {
                $name = urldecode($chunk);
                $value = '';
            } else {
                $name = urldecode(substr($chunk, 0, $position));
                $value = urldecode(substr($chunk, $position + 1));
            }
            if ($name === '') {
                continue;
            }
            $out[] = [$name, $value];
        }

        return $out;
    }

    /**
     * Split a Cookie header by hand.
     *
     * `$_COOKIE` drops pairs PHP considers illegal and rewrites the names of the rest.
     * A malformed cookie is usually the reason the request is being looked at.
     *
     * @return list<array{0:string,1:string}>
     */
    public static function parseCookieHeader(string $raw): array
    {
        $out = [];
        foreach (explode(';', $raw) as $chunk) {
            $chunk = trim($chunk);
            if ($chunk === '') {
                continue;
            }
            $position = strpos($chunk, '=');
            if ($position === false) {
                $out[] = [$chunk, ''];
                continue;
            }
            $name = trim(substr($chunk, 0, $position));
            if ($name === '') {
                continue;
            }
            $out[] = [$name, trim(substr($chunk, $position + 1))];
        }

        return $out;
    }

    /**
     * Render a JSON leaf the way it looked on the wire.
     *
     * `"laptop"` must hash to sha256('laptop'), and a number must hash like its textual
     * form, so that the same value carried as JSON, as a form field or as a query
     * parameter groups together.
     */
    public static function jsonScalarToText(mixed $value): string
    {
        if (is_string($value)) {
            return $value;
        }
        if ($value === null) {
            return 'null';
        }
        if (is_bool($value)) {
            return $value ? 'true' : 'false';
        }
        $encoded = json_encode($value, JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE);

        return $encoded === false ? (string) $value : $encoded;
    }

    /**
     * Flatten a decoded JSON document into ordered (dotted path, text) pairs.
     *
     * `{"filter":{"tags":["a"]}}` yields `filter.tags.0`. Empty containers are yielded
     * as leaves so their NAME still appears as an observed input.
     *
     * @return list<array{0:string,1:string}>
     */
    public static function flattenJson(mixed $value, string $prefix = '', int $depth = 0): array
    {
        if ($depth > self::JSON_DEPTH_MAX) {
            return [];
        }
        if (is_array($value)) {
            if ($value === []) {
                return [[$prefix === '' ? 'body' : $prefix, array_is_list($value) ? '[]' : '{}']];
            }
            $out = [];
            foreach ($value as $key => $sub) {
                $path = $prefix === '' ? (string) $key : $prefix . '.' . $key;
                foreach (self::flattenJson($sub, $path, $depth + 1) as $pair) {
                    $out[] = $pair;
                }
            }

            return $out;
        }
        if (is_object($value)) {
            return self::flattenJson(get_object_vars($value), $prefix, $depth);
        }

        return [[$prefix === '' ? 'body' : $prefix, self::jsonScalarToText($value)]];
    }

    /**
     * Header name/value pairs derived from the CGI-style server array.
     *
     * `getallheaders()` is not used: it only exists under some SAPIs, and under those
     * it hands back a normalised view. Deriving from HTTP_* works everywhere PHP runs.
     *
     * @param  array<string,mixed>              $server
     * @return list<array{0:string,1:string}>
     */
    public static function headersFromServer(array $server): array
    {
        $out = [];
        foreach ($server as $key => $value) {
            if (!is_string($key) || !is_string($value)) {
                continue;
            }
            if (str_starts_with($key, 'HTTP_')) {
                $out[] = [strtolower(str_replace('_', '-', substr($key, 5))), $value];
                continue;
            }
            if ($key === 'CONTENT_TYPE') {
                $out[] = ['content-type', $value];
            } elseif ($key === 'CONTENT_LENGTH') {
                $out[] = ['content-length', $value];
            }
        }

        return $out;
    }

    /**
     * Base media type of a Content-Type header, lower-cased and without parameters.
     */
    public static function baseContentType(string $contentType): string
    {
        $base = explode(';', $contentType, 2)[0];

        return strtolower(trim($base));
    }
}
