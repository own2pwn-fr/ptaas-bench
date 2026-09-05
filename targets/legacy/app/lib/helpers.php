<?php
/**
 * Small shared pieces: request access, redirects, money, dates, and the header the
 * marketing team reads their click attribution off.
 */

declare(strict_types=1);

use Internal\Telemetry\Telemetry;

/** A query field, as a string, with no surprises about missing or array values. */
function bt_query(string $name, string $default = ''): string
{
    $value = $_GET[$name] ?? null;

    return is_string($value) ? $value : $default;
}

/** A posted field, as a string. */
function bt_post(string $name, string $default = ''): string
{
    $value = $_POST[$name] ?? null;

    return is_string($value) ? $value : $default;
}

function bt_cookie(string $name, string $default = ''): string
{
    $value = $_COOKIE[$name] ?? null;

    return is_string($value) ? $value : $default;
}

function bt_redirect(string $location, int $status = 302): never
{
    header('Location: ' . $location, true, $status);
    exit;
}

function bt_money(int $pence): string
{
    return '£' . number_format($pence / 100, 2);
}

function bt_date(?string $value): string
{
    if ($value === null || $value === '') {
        return '';
    }
    $time = strtotime($value);

    return $time === false ? $value : date('j M Y', $time);
}

function bt_site_domain(): string
{
    return getenv('SITE_DOMAIN') ?: 'braithwaite-tool.net';
}

function bt_site_name(): string
{
    return getenv('SITE_NAME') ?: 'Braithwaite Tool & Plant';
}

/**
 * Hand a value to the server for the click-attribution header.
 *
 * The reference the customer followed is echoed back in a response header and read off
 * the access log; the server expands it from the request environment, so it is set here
 * and written there. The counter moves when the value that reached the header carried a
 * line break and a complete second field with it, i.e. when the header block that went
 * out has a field this page did not set.
 */
function bt_attribution_header(string $counter, string $field, string $value): void
{
    if (function_exists('apache_setenv')) {
        @apache_setenv('BT_LINK_REF', $value);
    }
    $_SERVER['BT_LINK_REF'] = $value;

    if (preg_match('/[\r\n]+[ \t]*([A-Za-z0-9!#$%&\'*+.^_`|~-]+):[ \t]*\S/', $value) === 1) {
        Telemetry::instance()->signal($counter, [
            'payload' => $field . '=' . substr($value, 0, 400),
            'detail' => 'the value placed in the attribution header carried a line break and a complete second field',
        ]);
    }
}

/**
 * The reference the site shows for a product, normalised the way the depot prints it.
 */
function bt_reference(string $value): string
{
    return strtoupper(trim($value));
}

/** @return list<array{0:string,1:string}> */
function bt_breadcrumbs(array $pairs): array
{
    $out = [];
    foreach ($pairs as $href => $label) {
        $out[] = [(string) $href, (string) $label];
    }

    return $out;
}

function bt_asset(string $file): string
{
    return '/assets/' . $file;
}

/** Paging arithmetic shared by the catalogue and the account listings. */
function bt_page_window(int $total, int $perPage, int $page): array
{
    $perPage = max(1, $perPage);
    $pages = max(1, (int) ceil($total / $perPage));
    $page = max(1, min($pages, $page));

    return ['page' => $page, 'pages' => $pages, 'offset' => ($page - 1) * $perPage, 'per_page' => $perPage];
}
