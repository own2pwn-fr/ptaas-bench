<?php
/**
 * The front controller's route table.
 *
 * The site was moved behind a single entry point during the 2016 rebuild, but the URLs
 * were left exactly as they were -- the trade customers have them bookmarked and the
 * printed catalogue has them on the back page -- so what is registered here is the same
 * set of .php paths the site has always had.
 *
 * A matched route reports its template, not the URL that was requested, so that the
 * per-endpoint numbers group the way an operator expects. A request that matches
 * nothing reports as unmatched, which is how the 404 rate is broken down by what people
 * were actually looking for.
 */

declare(strict_types=1);

use Internal\Telemetry\RequestContext;
use Internal\Telemetry\Telemetry;

final class BtRouter
{
    /** @var array<string, list<array{template:string,pattern:string,handler:callable,access:string}>> */
    private array $routes = [];

    public function add(string $method, string $template, callable $handler, string $access = 'public'): void
    {
        $this->routes[strtoupper($method)][] = [
            'template' => $template,
            'pattern' => self::compile($template),
            'handler' => $handler,
            'access' => $access,
        ];
    }

    public function get(string $template, callable $handler, string $access = 'public'): void
    {
        $this->add('GET', $template, $handler, $access);
    }

    public function post(string $template, callable $handler, string $access = 'public'): void
    {
        $this->add('POST', $template, $handler, $access);
    }

    /** Register the same handler for both verbs, which most of the forms here need. */
    public function form(string $template, callable $handler, string $access = 'public'): void
    {
        $this->add('GET', $template, $handler, $access);
        $this->add('POST', $template, $handler, $access);
    }

    private static function compile(string $template): string
    {
        $escaped = str_replace('#', '\#', $template);
        $pattern = preg_replace('/\{([a-z_]+)\}/', '(?P<$1>[^/]+)', $escaped);

        return '#^' . $pattern . '$#';
    }

    public function dispatch(string $method, string $path): void
    {
        $method = strtoupper($method);
        $telemetry = Telemetry::instance();

        foreach ($this->routes[$method] ?? [] as $route) {
            if (preg_match($route['pattern'], $path, $matches) !== 1) {
                continue;
            }
            $params = [];
            foreach ($matches as $key => $value) {
                if (is_string($key)) {
                    $params[$key] = $value;
                }
            }
            $telemetry->route($route['template'], $params);

            if ($route['access'] === 'account') {
                bt_session_start();
                bt_require_contact();
            } elseif ($route['access'] === 'staff') {
                bt_session_start();
                bt_require_staff();
            } elseif ($route['access'] === 'session') {
                bt_session_start();
            }

            ($route['handler'])($params);

            return;
        }

        // Nothing matched. The template stays unmatched, which is what the 404 report is
        // grouped on.
        $telemetry->route(RequestContext::UNMATCHED);
        http_response_code(404);
        bt_page('error', [
            'title' => 'Page not found',
            'message' => 'That page is not on the site. It may have moved when the catalogue was reorganised.',
        ]);
    }
}
