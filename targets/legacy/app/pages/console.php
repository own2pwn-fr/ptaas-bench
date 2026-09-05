<?php
/**
 * The staff console.
 *
 * It is the oldest part of the site and the least rewritten: operations run the depot
 * reports from here, and several of the pages still take a file name or a folder name
 * because that is how the people who use them think about the job.
 */

declare(strict_types=1);

function bt_page_admin_home(): void
{
    bt_page('admin-home', [
        'title' => 'Console',
        'counts' => [
            'orders' => (int) (bt_db_row('SELECT COUNT(*) AS n FROM orders')['n'] ?? 0),
            'enquiries' => (int) (bt_db_row('SELECT COUNT(*) AS n FROM enquiries')['n'] ?? 0),
            'customers' => (int) (bt_db_row('SELECT COUNT(*) AS n FROM customers')['n'] ?? 0),
            'lines' => (int) (bt_db_row('SELECT COUNT(*) AS n FROM products WHERE discontinued = 0')['n'] ?? 0),
        ],
        'panels' => [
            ['panels/stock-summary.php', 'Stock summary'],
            ['panels/depot-throughput.php', 'Depot throughput'],
            ['panels/open-quotations.php', 'Open quotations'],
            ['panels/carriage-spend.php', 'Carriage spend'],
        ],
    ]);
}

/**
 * A console panel.
 *
 * Panels were meant to be pluggable: drop a partial into the folder, or point one at
 * the reporting box on the other rack. The loader takes either.
 */
function bt_page_admin_widget(): void
{
    $source = bt_query('source', 'panels/stock-summary.php');

    bt_page('admin-widget', ['title' => 'Panel', 'source' => $source]);
}

/**
 * The storage report.
 *
 * There are two panes on this page, added a year apart. The one that is shown is the
 * older of them.
 */
function bt_page_admin_tools(): void
{
    $folder = $_SERVER['REQUEST_METHOD'] === 'POST' ? bt_post('folder', 'pod') : bt_query('folder', '');
    $report = null;

    if (trim($folder) !== '') {
        $report = bt_report_command(
            'console.storage.argument_break',
            'folder',
            'du -sh ' . BT_UPLOADS . '/%s 2>&1',
            $folder,
        );
    }

    bt_page('admin-tools', [
        'title' => 'Storage report',
        'folder' => $folder,
        'report' => $report,
        'folders' => ['pod', 'certificates', 'datasheets', 'photos'],
    ]);
}

/**
 * The report builder.
 *
 * The templates are a folder of partials that operations add to by hand, so the
 * selector carries a file name rather than an identifier from a list.
 */
function bt_page_admin_reports(): void
{
    $template = $_SERVER['REQUEST_METHOD'] === 'POST' ? bt_post('template', 'monthly-summary.php') : '';
    $period = bt_post('period', date('Y-m'));

    bt_page('admin-reports', [
        'title' => 'Reports',
        'template' => $template,
        'period' => $period,
        'templates' => [
            ['monthly-summary.php', 'Monthly summary'],
            ['depot-margins.php', 'Depot margins'],
            ['slow-movers.php', 'Slow-moving lines'],
            ['carriage-recovery.php', 'Carriage recovery'],
        ],
    ]);
}

/**
 * The enquiry list the sales desk works through every morning, and the export they take
 * into their own spreadsheet.
 */
function bt_page_admin_enquiries(): void
{
    $rows = bt_db_rows('SELECT * FROM enquiries ORDER BY created_at DESC LIMIT 500');

    if (bt_query('export') === 'csv') {
        header('Content-Type: text/csv; charset=utf-8');
        header('Content-Disposition: attachment; filename="enquiries.csv"');
        $out = fopen('php://output', 'wb');
        if ($out === false) {
            return;
        }
        fputcsv($out, ['Received', 'Kind', 'Name', 'Company', 'E-mail', 'Telephone', 'Message'], ',', '"', '\\');
        foreach ($rows as $row) {
            bt_csv_row($out, 'enquiries.export.formula_cell', [
                bt_date((string) $row['created_at']),
                (string) $row['kind'],
                (string) $row['name'],
                (string) $row['company'],
                (string) $row['email'],
                (string) $row['phone'],
                str_replace(["\r", "\n"], ' ', (string) $row['message']),
            ], [2, 3, 4, 6]);
        }
        fclose($out);

        return;
    }

    bt_page('admin-enquiries', ['title' => 'Enquiries', 'rows' => $rows]);
}

// ---------------------------------------------------------------- the rest of it

function bt_page_admin_customers(): void
{
    bt_page('admin-customers', [
        'title' => 'Customers',
        'customers' => bt_db_rows(
            'SELECT id, account_code, company, town, credit_limit_pence, balance_pence FROM customers ORDER BY company LIMIT 200',
        ),
    ]);
}

function bt_page_admin_customer(): void
{
    $customer = bt_db_row('SELECT * FROM customers WHERE account_code = ?', [bt_query('code')]);
    if ($customer === null) {
        http_response_code(404);
        bt_page('error', ['title' => 'Not found', 'message' => 'No customer with that account code.']);

        return;
    }
    bt_page('admin-customer', [
        'title' => $customer['company'],
        'customer' => $customer,
        'contacts' => bt_db_rows('SELECT name, email, phone, job_title FROM contacts WHERE customer_id = ?', [(int) $customer['id']]),
        'orders' => bt_db_rows('SELECT reference, placed_at, total_pence, status FROM orders WHERE customer_id = ? ORDER BY placed_at DESC LIMIT 20', [(int) $customer['id']]),
    ]);
}

function bt_page_admin_orders(): void
{
    $status = bt_query('status');
    $rows = $status === ''
        ? bt_db_rows('SELECT o.reference, o.placed_at, o.total_pence, o.status, c.company FROM orders o JOIN customers c ON c.id = o.customer_id ORDER BY o.placed_at DESC LIMIT 200')
        : bt_db_rows('SELECT o.reference, o.placed_at, o.total_pence, o.status, c.company FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.status = ? ORDER BY o.placed_at DESC LIMIT 200', [$status]);

    bt_page('admin-orders', ['title' => 'Orders', 'orders' => $rows, 'status' => $status]);
}

function bt_page_admin_order(): void
{
    $order = bt_db_row(
        'SELECT o.*, c.company, c.account_code FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.reference = ?',
        [bt_query('ref')],
    );
    if ($order === null) {
        http_response_code(404);
        bt_page('error', ['title' => 'Not found', 'message' => 'No order with that reference.']);

        return;
    }
    bt_page('admin-order', [
        'title' => 'Order ' . $order['reference'],
        'order' => $order,
        'lines' => bt_db_rows(
            'SELECT l.quantity, l.price_pence, p.reference, p.name FROM order_lines l JOIN products p ON p.id = l.product_id WHERE l.order_id = ?',
            [(int) $order['id']],
        ),
    ]);
}

function bt_page_admin_products(): void
{
    $q = bt_query('q');
    $rows = $q === ''
        ? bt_db_rows('SELECT id, reference, name, price_pence, stock, discontinued FROM products ORDER BY reference LIMIT 200')
        : bt_db_rows('SELECT id, reference, name, price_pence, stock, discontinued FROM products WHERE reference LIKE ? OR name LIKE ? ORDER BY reference LIMIT 200', ['%' . $q . '%', '%' . $q . '%']);

    bt_page('admin-products', ['title' => 'Products', 'products' => $rows, 'q' => $q]);
}

function bt_page_admin_product(): void
{
    $reference = $_SERVER['REQUEST_METHOD'] === 'POST' ? bt_post('reference') : bt_query('ref');
    $product = bt_db_row('SELECT * FROM products WHERE reference = ?', [bt_reference($reference)]);
    if ($product === null) {
        http_response_code(404);
        bt_page('error', ['title' => 'Not found', 'message' => 'No line with that reference.']);

        return;
    }
    $saved = false;
    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        bt_db_exec(
            'UPDATE products SET price_pence = ?, stock = ?, discontinued = ? WHERE id = ?',
            [
                max(0, (int) round(((float) bt_post('price', '0')) * 100)),
                max(0, (int) bt_post('stock', '0')),
                bt_post('discontinued') !== '' ? 1 : 0,
                (int) $product['id'],
            ],
        );
        $product = bt_db_row('SELECT * FROM products WHERE id = ?', [(int) $product['id']]);
        $saved = true;
    }
    bt_page('admin-product', ['title' => $product['name'], 'product' => $product, 'saved' => $saved]);
}

function bt_page_admin_settings(): void
{
    $saved = false;
    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        foreach (['carriage_free_over', 'cutoff_time', 'counter_open', 'notice'] as $key) {
            bt_db_exec(
                'INSERT INTO settings (name, value) VALUES (?, ?) ON DUPLICATE KEY UPDATE value = VALUES(value)',
                [$key, substr(bt_post($key), 0, 200)],
            );
        }
        $saved = true;
    }
    $rows = bt_db_rows('SELECT name, value FROM settings ORDER BY name');
    $settings = [];
    foreach ($rows as $row) {
        $settings[(string) $row['name']] = (string) $row['value'];
    }
    bt_page('admin-settings', ['title' => 'Settings', 'settings' => $settings, 'saved' => $saved]);
}

function bt_page_admin_audit(): void
{
    bt_page('admin-audit', [
        'title' => 'Recent activity',
        'rows' => bt_db_rows('SELECT created_at, actor, action, detail FROM audit_log ORDER BY created_at DESC LIMIT 200'),
    ]);
}

function bt_page_admin_branches(): void
{
    bt_page('admin-branches', [
        'title' => 'Depots',
        'branches' => bt_db_rows('SELECT id, name, town, postcode, phone, manager FROM branches ORDER BY name'),
    ]);
}
