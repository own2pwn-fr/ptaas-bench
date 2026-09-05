<?php

declare(strict_types=1);

/**
 * The whole wiring, for a service that owns its front controller.
 *
 * Two calls at the top of the request and one at the point the router knows what it
 * matched. Everything else -- the input enumeration, the single record per request,
 * the classification of the traffic -- follows from those.
 *
 * A service that has no front controller of its own, or one whose docroot contains
 * scripts the router never sees, wires the same two calls through php.ini instead:
 *
 *     auto_prepend_file = /srv/app/telemetry-prepend.php
 *
 * with a prepend that does the init() and the observe() and nothing else. Requests that
 * never reach the router are then recorded too, under the `<unmatched>` template, which
 * is usually the more interesting half of the traffic.
 */

require __DIR__ . '/../vendor/autoload.php';

use Internal\Telemetry\Telemetry;

$telemetry = Telemetry::init();   // TELEMETRY_SERVICE, TELEMETRY_ENDPOINT, ...
$telemetry->observe();            // capture the request; the record is written at teardown

// ---------------------------------------------------------------- a tiny router

/** @var array<string, callable(array<string,string>):void> $routes */
$routes = [
    '/' => static function (): void {
        echo 'home';
    },
    '/orders/{id}' => static function (array $params) use ($telemetry): void {
        $telemetry->authSubject('customer:1041');
        echo 'order ', htmlspecialchars($params['id'], ENT_QUOTES, 'UTF-8');
    },
];

$path = explode('?', $_SERVER['REQUEST_URI'] ?? '/', 2)[0];

foreach ($routes as $template => $handler) {
    // Templates are plain paths, so this stays readable: each {name} becomes a named
    // capture and everything else is matched literally.
    $pattern = '#^' . preg_replace('/\{([a-z_]+)\}/', '(?P<$1>[^/]+)', str_replace('#', '\#', $template)) . '$#';
    if (preg_match($pattern, $path, $matches) === 1) {
        $params = array_filter($matches, 'is_string', ARRAY_FILTER_USE_KEY);
        // The template, never the concrete URL: one series per endpoint, not one per id.
        $telemetry->route($template, $params);
        $handler($params);
        exit;
    }
}

http_response_code(404);
echo 'not found';

// ------------------------------------------------------- raising a signal, later
//
// Anywhere in the call stack below the router, with no plumbing and no way to get the
// classification of the traffic wrong:
//
//     Telemetry::instance()->signal('orders.export.formula_cell', [
//         'payload' => $cell,
//         'detail'  => 'exported cell opens with a calculation prefix',
//     ]);
//
// And immediately before a fetch whose destination came from the request:
//
//     Telemetry::instance()->outbound($url, signal: 'imports.feed.fetch_external', param: 'source');
