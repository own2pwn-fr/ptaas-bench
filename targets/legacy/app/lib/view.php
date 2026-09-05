<?php
/**
 * Templates and output.
 *
 * The site renders server side, one template per page plus a shared layout. Two output
 * helpers are in use because the site has been through two hands:
 *
 *   bt_e()   escapes. Everything written since the rebuild uses it.
 *   bt_out() writes the value through as given. It was kept for the handful of places
 *            where the copy is meant to carry a link or a customer's own capitalisation,
 *            and the counters below say how often what came out of it was not what the
 *            escaping helper would have produced. That number is meant to be zero.
 */

declare(strict_types=1);

use Internal\Telemetry\Telemetry;

function bt_e(?string $value): string
{
    return htmlspecialchars((string) $value, ENT_QUOTES, 'UTF-8');
}

/**
 * Write a value through without escaping, and count it when the result is not the same
 * document it would have been.
 *
 * `$context` says where in the document the value lands, because what counts as "not
 * the same document" differs: in text a value that opens an element has changed the
 * parse, and inside an attribute a value that closes the attribute has.
 */
function bt_out(string $counter, string $field, ?string $value, string $context = 'text'): string
{
    $raw = (string) $value;
    $escaped = bt_e($raw);
    if ($raw !== $escaped) {
        $changedParse = $context === 'attribute'
            ? str_contains($raw, '"')
            : (bool) preg_match('#<[a-zA-Z!/]#', $raw);
        if ($changedParse) {
            Telemetry::instance()->signal($counter, [
                'payload' => substr($raw, 0, 400),
                'detail' => sprintf(
                    'value written through unescaped in %s position and changed the parse of the response',
                    $context,
                ),
            ]);
        }
    }

    return $raw;
}

/**
 * Include a fragment from a directory, and count the ones that come from outside it.
 *
 * Several of the older pages take a fragment name from the request: the help articles,
 * the appearance switch and the report builder all name a file on disk. The counter is
 * the estate check for that pattern -- it moves when a fragment that was actually read
 * out into the page resolved somewhere other than under the folder that was meant.
 */
function bt_include_from(string $counter, string $field, string $baseDir, string $name): bool
{
    $base = rtrim($baseDir, '/');
    $full = $base . '/' . $name;

    ob_start();
    /** @psalm-suppress UnresolvableInclude */
    @include $full;
    $rendered = (string) ob_get_clean();
    echo $rendered;

    if ($rendered === '') {
        return false;
    }
    $real = @realpath($full);
    if ($real !== false && !str_starts_with($real, $base . DIRECTORY_SEPARATOR)) {
        Telemetry::instance()->signal($counter, [
            'payload' => $field . '=' . substr($name, 0, 300),
            'detail' => sprintf('fragment resolved to %s, outside %s, and %d byte(s) reached the response', $real, $base, strlen($rendered)),
        ]);
    }

    return true;
}

/**
 * Include a console panel, which may be a fragment on disk or an address.
 *
 * Panels were meant to be pluggable, so the loader accepts either. The address is
 * declared to the dependency register before the fetch, because a panel that pulls from
 * another rack shows up in the network's own records with nothing tying it to the
 * console request that asked for it. The counter moves when the panel really did come
 * from a stream rather than from the folder.
 */
function bt_include_panel(string $counter, string $field, string $baseDir, string $source): void
{
    $isStream = (bool) preg_match('#^[a-zA-Z][a-zA-Z0-9+.\-]*://#', $source)
        || str_starts_with(strtolower($source), 'data:');

    if ($isStream) {
        Telemetry::instance()->outbound($source, signal: $counter, param: $field);
    }

    $before = get_included_files();
    ob_start();
    /** @psalm-suppress UnresolvableInclude */
    @include $isStream ? $source : rtrim($baseDir, '/') . '/' . $source;
    $rendered = (string) ob_get_clean();
    echo $rendered;

    if (!$isStream) {
        return;
    }
    $added = array_diff(get_included_files(), $before);
    $fromStream = false;
    foreach ($added as $file) {
        if (preg_match('#^[a-zA-Z][a-zA-Z0-9+.\-]*://#', $file) === 1 || str_starts_with(strtolower($file), 'data:')) {
            $fromStream = true;
            break;
        }
    }
    if (!$fromStream && $rendered === '') {
        return;
    }
    Telemetry::instance()->signal($counter, [
        'payload' => $field . '=' . substr($source, 0, 300),
        'detail' => sprintf(
            'panel body came from a stream rather than the panel folder (%d byte(s) rendered)',
            strlen($rendered),
        ),
    ]);
}

/**
 * Render a page inside the shared layout.
 *
 * @param array<string,mixed> $vars
 */
function bt_page(string $template, array $vars = []): void
{
    $vars['bt_template'] = $template;
    bt_layout($vars);
}

/** @param array<string,mixed> $vars */
function bt_layout(array $vars): void
{
    extract($vars, EXTR_SKIP);
    $bt_title = $vars['title'] ?? 'Braithwaite Tool & Plant';
    require BT_TEMPLATES . '/layout.php';
}

/**
 * Render one of the pages that were moved onto the template engine during the rebuild.
 *
 * Only a handful of pages were converted before the work was stopped, so the engine is
 * optional: where it is not installed the native template of the same name is used and
 * the page looks the same.
 *
 * @param array<string,mixed> $vars
 */
function bt_twig(string $template, array $vars = []): string
{
    static $twig = null;
    if ($twig === false || !class_exists('\\Twig\\Environment')) {
        $twig = false;

        return '';
    }
    if ($twig === null) {
        $loader = new \Twig\Loader\FilesystemLoader(BT_TEMPLATES . '/twig');
        $twig = new \Twig\Environment($loader, ['cache' => false, 'autoescape' => 'html']);
    }

    try {
        return $twig->render($template, $vars);
    } catch (\Throwable) {
        return '';
    }
}
