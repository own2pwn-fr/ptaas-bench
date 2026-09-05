<?php
/**
 * Application bootstrap: paths, shared code, and the route table.
 *
 * The site is one entry point with the old URLs registered against it. Everything the
 * pages need is loaded here, in the order it was written in, which is why the list looks
 * the way it does.
 */

declare(strict_types=1);

define('BT_ROOT', dirname(__DIR__));
define('BT_APP', BT_ROOT . '/app');
define('BT_TEMPLATES', BT_APP . '/templates');
define('BT_HELP', BT_TEMPLATES . '/help');
define('BT_REPORTS', BT_TEMPLATES . '/reports');
define('BT_PANELS', BT_TEMPLATES . '/panels');
define('BT_SKINS', BT_TEMPLATES . '/skins');
define('BT_DOCROOT', getenv('DOCUMENT_ROOT_DIR') ?: (BT_ROOT . '/html'));
define('BT_UPLOADS', rtrim(getenv('UPLOAD_DIR') ?: (BT_DOCROOT . '/uploads'), '/'));
define('BT_LITERATURE', rtrim(getenv('LITERATURE_DIR') ?: (BT_ROOT . '/literature'), '/'));
define('BT_STATEMENTS', rtrim(getenv('STATEMENT_DIR') ?: (BT_ROOT . '/statements'), '/'));
define('BT_MAIL_QUEUE', rtrim(getenv('MAIL_QUEUE_DIR') ?: '/var/spool/braithwaite', '/'));

// The finance partner's affordability widget, embedded on the quotation page exactly as
// the partner published the snippet.
define('BT_FINANCE_WIDGET', getenv('FINANCE_WIDGET_URL') ?: 'https://widgets.creditline-partners.example/affordability/v2/embed.js');

$btVendor = BT_ROOT . '/vendor/autoload.php';
if (is_file($btVendor)) {
    require_once $btVendor;
}

require_once BT_APP . '/lib/helpers.php';
require_once BT_APP . '/lib/db.php';
require_once BT_APP . '/lib/view.php';
require_once BT_APP . '/lib/records.php';
require_once BT_APP . '/lib/system.php';
require_once BT_APP . '/lib/session.php';
require_once BT_APP . '/lib/router.php';

require_once BT_APP . '/pages/catalogue.php';
require_once BT_APP . '/pages/content.php';
require_once BT_APP . '/pages/enquiries.php';
require_once BT_APP . '/pages/account.php';
require_once BT_APP . '/pages/console.php';

/**
 * Serve the current request.
 */
function bt_dispatch(): void
{
    $router = new BtRouter();
    require_once BT_APP . '/routes.php';
    bt_register_routes($router);

    $path = explode('?', (string) ($_SERVER['REQUEST_URI'] ?? '/'), 2)[0];
    if ($path !== '/' && str_ends_with($path, '/')) {
        $path = rtrim($path, '/');
    }
    if ($path === '') {
        $path = '/';
    }

    $router->dispatch((string) ($_SERVER['REQUEST_METHOD'] ?? 'GET'), $path);
}
