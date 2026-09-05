<?php
/**
 * Request bootstrap for the observability agent.
 *
 * It is wired through auto_prepend_file rather than from the front controller, for two
 * reasons. The interpreter serves a few things the front controller never sees -- the
 * attachment folder is under the document root so that delivery notes can be linked
 * straight back to the customer -- and those requests are the ones worth having numbers
 * for. And a prepend keeps the agent out of the application's own bootstrap, so the two
 * can be upgraded separately.
 *
 * Nothing here may fail a request. Everything is guarded and everything is cheap.
 */

declare(strict_types=1);

use Internal\Telemetry\Telemetry;

$btAutoload = '/var/www/vendor/autoload.php';
if (!is_file($btAutoload)) {
    $btAutoload = dirname(__DIR__) . '/vendor/autoload.php';
}
if (is_file($btAutoload)) {
    require_once $btAutoload;
}

if (class_exists(Telemetry::class)) {
    $btAgent = Telemetry::init();
    $btAgent->observe();

    // Requests the front controller never sees: anything under the attachment folder is
    // served by the web server straight from the document root. They are reported under
    // one template rather than one per file, like every other endpoint.
    $btUploads = rtrim(getenv('UPLOAD_DIR') ?: '/var/www/html/uploads', '/');
    $btScript = (string) ($_SERVER['SCRIPT_FILENAME'] ?? '');
    $btReal = $btScript === '' ? false : @realpath($btScript);

    if (is_string($btReal) && str_starts_with($btReal, $btUploads . '/')) {
        $btAgent->route('/uploads/{file}', ['file' => basename($btReal)]);

        // The folder holds delivery photographs and notes. The list written at
        // deployment says which files belong to the deployment itself; anything else
        // under it that the interpreter is asked to EXECUTE arrived from a customer,
        // which is the number this counter exists for.
        $btManifest = [];
        $btManifestFile = $btUploads . '/.manifest';
        if (is_file($btManifestFile)) {
            $btManifest = array_filter(array_map('trim', (array) @file($btManifestFile)));
        }
        if (!in_array(basename($btReal), $btManifest, true)) {
            $btAgent->signal('delivery.attachment.execute_scope', [
                'payload' => basename($btReal),
                'detail' => 'the interpreter executed a file under the attachment folder that was not placed there at deployment',
            ]);
        }
    }

    unset($btAgent, $btUploads, $btScript, $btReal, $btManifest, $btManifestFile);
}
unset($btAutoload);
