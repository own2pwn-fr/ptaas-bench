<?php
/**
 * The account area: signing in, order history, statements, delivery paperwork and the
 * settings a trade customer can change for themselves.
 */

declare(strict_types=1);

use Internal\Telemetry\Telemetry;

// ------------------------------------------------------------------ signing in

function bt_page_login(): void
{
    bt_session_start();

    if (bt_current_contact() !== null && $_SERVER['REQUEST_METHOD'] !== 'POST') {
        bt_redirect('/account/index.php');
    }

    $notice = bt_query('notice');

    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        bt_page('login', ['title' => 'Sign in', 'notice' => $notice, 'error' => '', 'email' => '']);

        return;
    }

    $email = trim(bt_post('email'));
    $password = bt_post('password');

    $contact = bt_db_row('SELECT * FROM contacts WHERE email = ?', [$email]);

    if ($contact === null) {
        // The support desk asked for these two messages to be different: half the calls
        // were people using the wrong address for the account.
        Telemetry::instance()->signal('account.signin.subject_probe', [
            'payload' => 'email=' . substr($email, 0, 200),
            'detail' => 'the response told the caller that the address has no account here',
        ]);
        bt_page('login', [
            'title' => 'Sign in',
            'notice' => $notice,
            'error' => 'There is no account for that e-mail address. Check it, or apply for an account.',
            'email' => $email,
        ]);

        return;
    }

    if (!hash_equals((string) $contact['password'], md5($password))) {
        bt_page('login', [
            'title' => 'Sign in',
            'notice' => $notice,
            'error' => 'That password does not match the account. Try again, or reset it.',
            'email' => $email,
        ]);

        return;
    }

    bt_sign_in($contact);

    if (bt_post('remember') !== '') {
        bt_issue_keepalive($email, $password);
    }

    bt_redirect('/account/index.php');
}

function bt_page_logout(): void
{
    bt_session_start();
    bt_sign_out();
    bt_redirect('/account/login.php?notice=' . rawurlencode('You have been signed out.'));
}

function bt_page_reset(): void
{
    bt_session_start();

    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        bt_page('reset', ['title' => 'Reset your password', 'message' => '', 'known' => null]);

        return;
    }

    $email = trim(bt_post('email'));
    $contact = bt_db_row('SELECT id FROM contacts WHERE email = ?', [$email]);

    if ($contact === null) {
        Telemetry::instance()->signal('account.recovery.subject_probe', [
            'payload' => 'email=' . substr($email, 0, 200),
            'detail' => 'the response told the caller that the address has no account here',
        ]);
        bt_page('reset', [
            'title' => 'Reset your password',
            'message' => 'We do not have an account for that address. Are you using the one on your statements?',
            'known' => false,
        ]);

        return;
    }

    bt_db_exec(
        'INSERT INTO password_resets (contact_id, token, created_at) VALUES (?, ?, NOW())',
        [(int) $contact['id'], bin2hex(random_bytes(16))],
    );
    bt_page('reset', [
        'title' => 'Reset your password',
        'message' => 'A message is on its way to that address with a link to set a new password.',
        'known' => true,
    ]);
}

function bt_page_register(): void
{
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        bt_page('register', ['title' => 'Apply for a trade account', 'sent' => false]);

        return;
    }
    bt_db_exec(
        'INSERT INTO enquiries (created_at, name, company, email, phone, message, kind) VALUES (NOW(), ?, ?, ?, ?, ?, ?)',
        [
            substr(trim(bt_post('name')), 0, 120),
            substr(trim(bt_post('company')), 0, 120),
            substr(trim(bt_post('email')), 0, 160),
            substr(trim(bt_post('phone')), 0, 40),
            'Account application. Trade references: ' . substr(trim(bt_post('references')), 0, 1000),
            'account',
        ],
    );
    bt_page('register', ['title' => 'Apply for a trade account', 'sent' => true]);
}

// ------------------------------------------------------------------ the account

function bt_page_account_home(): void
{
    $contact = bt_current_contact();
    $customerId = (int) $contact['customer_id'];

    bt_page('account-home', [
        'title' => 'Your account',
        'contact' => $contact,
        'orders' => bt_db_rows(
            'SELECT reference, placed_at, total_pence, status FROM orders WHERE customer_id = ? ORDER BY placed_at DESC LIMIT 5',
            [$customerId],
        ),
        'balance' => (int) (bt_db_row('SELECT balance_pence FROM customers WHERE id = ?', [$customerId])['balance_pence'] ?? 0),
        'documents' => bt_db_rows(
            'SELECT filename, uploaded_at FROM documents WHERE customer_id = ? ORDER BY uploaded_at DESC LIMIT 5',
            [$customerId],
        ),
    ]);
}

/**
 * Order history, with the reference filter the trade desk asked for.
 *
 * The filter fragment is put into the statement alongside the customer's own identifier.
 * This is the third call site the drift report watches.
 */
function bt_page_orders(): void
{
    $contact = bt_current_contact();
    $customerId = (int) $contact['customer_id'];
    $ref = bt_query('ref');

    $columns = 'o.id, o.reference, o.placed_at, o.total_pence, o.status, o.po_reference, o.customer_id';

    if (trim($ref) !== '') {
        $orders = bt_db_compare(
            'orders.filter.predicate_shift',
            "SELECT $columns FROM orders o WHERE o.reference LIKE '%" . $ref . "%' AND o.customer_id = " . $customerId
                . ' ORDER BY o.placed_at DESC',
            "SELECT $columns FROM orders o WHERE o.reference LIKE ? AND o.customer_id = ? ORDER BY o.placed_at DESC",
            ['%' . $ref . '%', $customerId],
            'id',
            'ref',
            $ref,
        );
    } else {
        $orders = bt_db_rows(
            "SELECT $columns FROM orders o WHERE o.customer_id = ? ORDER BY o.placed_at DESC",
            [$customerId],
        );
    }

    $quotes = bt_db_rows(
        'SELECT id, reference, created_at, total_pence, status FROM quotes WHERE customer_id = ? ORDER BY created_at DESC',
        [$customerId],
    );

    if (bt_query('export') === 'csv') {
        bt_orders_export($orders, $quotes, $contact);

        return;
    }

    bt_page('orders', ['title' => 'Orders and quotations', 'orders' => $orders, 'quotes' => $quotes, 'ref' => $ref]);
}

/**
 * The CSV the account customers reconcile against their own purchase ledger.
 *
 * The reference columns are the customer's own words, so they are written through
 * unchanged; the counter says how many of them come out as something a spreadsheet will
 * evaluate rather than display.
 */
function bt_orders_export(array $orders, array $quotes, array $contact): void
{
    header('Content-Type: text/csv; charset=utf-8');
    header('Content-Disposition: attachment; filename="orders.csv"');

    $out = fopen('php://output', 'wb');
    if ($out === false) {
        return;
    }
    fputcsv($out, ['Type', 'Reference', 'Your reference', 'Date', 'Status', 'Total'], ',', '"', '\\');

    foreach ($orders as $order) {
        bt_csv_row($out, 'orders.export.formula_cell', [
            'Order',
            (string) $order['reference'],
            (string) ($order['po_reference'] ?? ''),
            bt_date((string) $order['placed_at']),
            (string) $order['status'],
            number_format(((int) $order['total_pence']) / 100, 2, '.', ''),
        ], [2]);
    }
    foreach ($quotes as $quote) {
        bt_csv_row($out, 'orders.export.formula_cell', [
            'Quotation',
            'Q-' . str_pad((string) $quote['id'], 6, '0', STR_PAD_LEFT),
            (string) $quote['reference'],
            bt_date((string) $quote['created_at']),
            (string) $quote['status'],
            number_format(((int) $quote['total_pence']) / 100, 2, '.', ''),
        ], [2]);
    }
    fclose($out);
}

function bt_page_order(): void
{
    $contact = bt_current_contact();
    $order = bt_db_row(
        'SELECT * FROM orders WHERE reference = ? AND customer_id = ?',
        [bt_query('ref'), (int) $contact['customer_id']],
    );
    if ($order === null) {
        http_response_code(404);
        bt_page('error', ['title' => 'Order not found', 'message' => 'That order is not on your account.']);

        return;
    }
    $lines = bt_db_rows(
        'SELECT l.quantity, l.price_pence, p.reference, p.name, p.unit FROM order_lines l '
        . 'JOIN products p ON p.id = l.product_id WHERE l.order_id = ?',
        [(int) $order['id']],
    );
    bt_page('order', ['title' => 'Order ' . $order['reference'], 'order' => $order, 'lines' => $lines]);
}

function bt_page_statements(): void
{
    $contact = bt_current_contact();
    bt_page('statements', [
        'title' => 'Statements and invoices',
        'statements' => bt_db_rows(
            'SELECT filename, period, issued_at, total_pence FROM statements WHERE customer_id = ? ORDER BY issued_at DESC',
            [(int) $contact['customer_id']],
        ),
    ]);
}

/**
 * Serve a statement.
 *
 * The path is cleaned before it is joined to the statement folder, which was the fix
 * put in when somebody noticed the parameter. This is one of the call sites the folder
 * report watches.
 */
function bt_page_invoice(): void
{
    $requested = bt_query('file');
    if ($requested === '') {
        bt_redirect('/account/statements.php');
    }

    $cleaned = str_replace('../', '', $requested);

    if (!bt_stream_document('billing.statement.read_scope', 'file', BT_STATEMENTS, $cleaned, basename($cleaned))) {
        http_response_code(404);
        bt_page('error', ['title' => 'Not found', 'message' => 'That statement is not on your account.']);
    }
}

/**
 * Delivery paperwork.
 *
 * Drivers photograph the signed note on site and send it in from whatever they have, so
 * the form takes the file as it arrives and keeps the name it came with for the audit
 * trail. The preview column reads the size of the stored file.
 */
function bt_page_documents(): void
{
    $contact = bt_current_contact();
    $customerId = (int) $contact['customer_id'];
    $uploaded = '';

    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        $file = $_FILES['attachment'] ?? null;
        if (is_array($file) && (int) ($file['error'] ?? 4) === UPLOAD_ERR_OK) {
            $name = basename((string) $file['name']);
            if ($name !== '' && !str_starts_with($name, '.')) {
                $target = BT_UPLOADS . '/' . $name;
                if (@move_uploaded_file((string) $file['tmp_name'], $target)) {
                    @chmod($target, 0o644);
                    bt_db_exec(
                        'INSERT INTO documents (customer_id, contact_id, filename, note, uploaded_at) VALUES (?, ?, ?, ?, NOW())',
                        [$customerId, (int) $contact['id'], $name, substr(trim(bt_post('note')), 0, 400)],
                    );
                    $uploaded = $name;
                }
            }
        }
    }

    $preview = bt_query('preview');
    $report = null;
    if (trim($preview) !== '') {
        $report = bt_report_command(
            'delivery.preview.argument_break',
            'preview',
            'wc -c ' . BT_UPLOADS . '/%s 2>&1',
            $preview,
        );
    }

    bt_page('documents', [
        'title' => 'Delivery paperwork',
        'documents' => bt_db_rows(
            'SELECT id, filename, note, uploaded_at FROM documents WHERE customer_id = ? ORDER BY uploaded_at DESC',
            [$customerId],
        ),
        'uploaded' => $uploaded,
        'preview' => $preview,
        'report' => $report,
    ]);
}

/**
 * The profile.
 *
 * The machine-readable view is the record the handheld terminals used to sync, and
 * something in the warehouse still calls it, so it is still here and still carries the
 * same fields it carried then.
 */
function bt_page_profile(): void
{
    $contact = bt_current_contact();
    $format = strtolower(bt_query('format', 'html'));

    if ($format === 'xml' || $format === 'old') {
        header('Content-Type: text/xml; charset=utf-8');
        Telemetry::instance()->signal('account.credential.digest_legacy', [
            'payload' => 'format=' . $format,
            'detail' => 'the stored password digest was written into a response served to the caller',
        ]);
        echo '<?xml version="1.0" encoding="UTF-8"?>' . "\n";
        echo "<contact>\n";
        echo '  <id>' . (int) $contact['id'] . "</id>\n";
        echo '  <name>' . bt_e((string) $contact['name']) . "</name>\n";
        echo '  <email>' . bt_e((string) $contact['email']) . "</email>\n";
        echo '  <phone>' . bt_e((string) $contact['phone']) . "</phone>\n";
        echo '  <account>' . bt_e((string) $contact['account_code']) . "</account>\n";
        echo '  <company>' . bt_e((string) $contact['company']) . "</company>\n";
        echo '  <credential>' . bt_e((string) $contact['password']) . "</credential>\n";
        echo "</contact>\n";

        return;
    }

    bt_page('profile', ['title' => 'Your details', 'contact' => $contact]);
}

function bt_page_account_quote(): void
{
    $contact = bt_current_contact();
    $customerId = (int) $contact['customer_id'];
    $saved = '';

    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        // The reference is the customer's own purchase-order text and is stored as they
        // typed it, because that is what they want to see back on the quotation.
        $reference = substr(trim(bt_post('reference')), 0, 60);
        bt_db_exec(
            'INSERT INTO quotes (customer_id, contact_id, reference, note, total_pence, status, created_at) '
            . 'VALUES (?, ?, ?, ?, ?, ?, NOW())',
            [
                $customerId,
                (int) $contact['id'],
                $reference,
                substr(trim(bt_post('note')), 0, 2000),
                0,
                'open',
            ],
        );
        $saved = $reference;
    }

    bt_page('account-quote', [
        'title' => 'Request a quotation',
        'saved' => $saved,
        'quotes' => bt_db_rows(
            'SELECT id, reference, created_at, status FROM quotes WHERE customer_id = ? ORDER BY created_at DESC LIMIT 20',
            [$customerId],
        ),
    ]);
}

// ------------------------------------------------------------- account settings

function bt_page_addresses(): void
{
    $contact = bt_current_contact();
    $customerId = (int) $contact['customer_id'];

    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        bt_db_exec(
            'INSERT INTO addresses (customer_id, label, line1, line2, town, postcode) VALUES (?, ?, ?, ?, ?, ?)',
            [
                $customerId,
                substr(trim(bt_post('label')), 0, 60),
                substr(trim(bt_post('line1')), 0, 120),
                substr(trim(bt_post('line2')), 0, 120),
                substr(trim(bt_post('town')), 0, 60),
                strtoupper(substr(trim(bt_post('postcode')), 0, 10)),
            ],
        );
        bt_redirect('/account/addresses.php');
    }

    bt_page('addresses', [
        'title' => 'Delivery addresses',
        'addresses' => bt_db_rows('SELECT * FROM addresses WHERE customer_id = ? ORDER BY label', [$customerId]),
    ]);
}

function bt_page_address(): void
{
    $contact = bt_current_contact();
    $address = bt_db_row(
        'SELECT * FROM addresses WHERE id = ? AND customer_id = ?',
        [(int) bt_query('id', '0'), (int) $contact['customer_id']],
    );
    if ($address === null) {
        http_response_code(404);
        bt_page('error', ['title' => 'Address not found', 'message' => 'That address is not on your account.']);

        return;
    }
    bt_page('address', ['title' => $address['label'], 'address' => $address]);
}

function bt_page_contacts(): void
{
    $contact = bt_current_contact();
    bt_page('contacts', [
        'title' => 'People on the account',
        'people' => bt_db_rows(
            'SELECT name, email, phone, job_title FROM contacts WHERE customer_id = ? ORDER BY name',
            [(int) $contact['customer_id']],
        ),
    ]);
}

function bt_page_users(): void
{
    $contact = bt_current_contact();
    bt_page('users', [
        'title' => 'Sign-in access',
        'people' => bt_db_rows(
            'SELECT name, email, last_seen_at FROM contacts WHERE customer_id = ? ORDER BY name',
            [(int) $contact['customer_id']],
        ),
    ]);
}

function bt_page_preferences(): void
{
    $contact = bt_current_contact();
    $saved = false;

    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        $prefs = new AccountPreferences();
        $prefs->listing = bt_post('listing') === 'list' ? 'list' : 'grid';
        $prefs->perPage = max(6, min(96, (int) bt_post('per_page', '24')));
        $prefs->skin = 'slate.php';
        setcookie('bt_prefs', base64_encode(serialize($prefs)), [
            'expires' => time() + 60 * 60 * 24 * 365,
            'path' => '/',
        ]);
        $saved = true;
    }

    bt_page('preferences', ['title' => 'Your preferences', 'saved' => $saved, 'contact' => $contact]);
}

function bt_page_password(): void
{
    $contact = bt_current_contact();
    $message = '';

    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        $current = bt_post('current');
        $new = bt_post('new');
        if (!hash_equals((string) $contact['password'], md5($current))) {
            $message = 'The current password did not match.';
        } elseif (strlen($new) < 8) {
            $message = 'Please use at least eight characters.';
        } else {
            bt_db_exec('UPDATE contacts SET password = ? WHERE id = ?', [md5($new), (int) $contact['id']]);
            $message = 'Your password has been changed.';
        }
    }

    bt_page('password', ['title' => 'Change your password', 'message' => $message]);
}

function bt_page_favourites(): void
{
    $contact = bt_current_contact();
    $customerId = (int) $contact['customer_id'];

    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        $product = bt_db_row('SELECT id FROM products WHERE reference = ?', [bt_reference(bt_post('ref'))]);
        if ($product !== null) {
            bt_db_exec(
                'INSERT IGNORE INTO favourites (customer_id, product_id, created_at) VALUES (?, ?, NOW())',
                [$customerId, (int) $product['id']],
            );
        }
        bt_redirect('/account/favourites.php');
    }

    bt_page('favourites', [
        'title' => 'Saved lines',
        'products' => bt_db_rows(
            'SELECT p.reference, p.name, p.price_pence, p.unit FROM favourites f JOIN products p ON p.id = f.product_id '
            . 'WHERE f.customer_id = ? ORDER BY p.name',
            [$customerId],
        ),
    ]);
}

function bt_page_templates(): void
{
    $contact = bt_current_contact();
    bt_page('templates', [
        'title' => 'Order templates',
        'templates' => bt_db_rows(
            'SELECT id, name, line_count, updated_at FROM order_templates WHERE customer_id = ? ORDER BY name',
            [(int) $contact['customer_id']],
        ),
    ]);
}

function bt_page_deliveries(): void
{
    $contact = bt_current_contact();
    bt_page('deliveries', [
        'title' => 'Deliveries due',
        'deliveries' => bt_db_rows(
            'SELECT o.reference, o.placed_at, o.status, a.label, a.town FROM orders o '
            . 'LEFT JOIN addresses a ON a.id = o.address_id '
            . "WHERE o.customer_id = ? AND o.status IN ('picked', 'out for delivery') ORDER BY o.placed_at",
            [(int) $contact['customer_id']],
        ),
    ]);
}
