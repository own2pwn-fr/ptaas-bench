<?php
/**
 * The public catalogue: the front page, sections, products, stock, depots, literature
 * and the basket.
 *
 * These are the oldest pages on the site. Several of them still build their own
 * statements and take a file name from the request, which is why they carry more of the
 * estate counters than the pages written after the rebuild.
 */

declare(strict_types=1);

use Internal\Telemetry\Telemetry;

// ---------------------------------------------------------------- front page

function bt_page_home(): void
{
    // The saved appearance record travels in a cookie so it survives the nightly
    // session sweep. Restoring it here means every page gets the customer's own
    // listing choice, not just the account area.
    $prefs = bt_restore(
        'preferences.restore.lifecycle_call',
        'bt_prefs',
        [AccountPreferences::class],
        bt_cookie('bt_prefs'),
    );
    if (!($prefs instanceof AccountPreferences)) {
        $prefs = new AccountPreferences();
    }
    $GLOBALS['bt_prefs'] = $prefs;

    $offers = bt_db_rows(
        'SELECT id, reference, name, price_pence, was_pence FROM products WHERE on_offer = 1 ORDER BY id LIMIT 8',
    );
    $sections = bt_db_rows('SELECT slug, name, blurb FROM categories ORDER BY sort_order, id LIMIT 9');
    $news = bt_db_rows('SELECT slug, title, published_at, summary FROM news ORDER BY published_at DESC LIMIT 3');

    bt_page('home', [
        'title' => bt_site_name() . ' — trade tools, fixings and plant consumables',
        'offers' => $offers,
        'sections' => $sections,
        'news' => $news,
        'prefs' => $prefs,
    ]);
}

// ---------------------------------------------------------------- catalogue

function bt_page_catalogue(): void
{
    $section = bt_query('section');
    $page = max(1, (int) bt_query('page', '1'));
    $sort = bt_query('sort', 'name');

    // Sorting is chosen from a list, not built from the request: the column names are
    // ours and the direction is fixed per key.
    $orderBy = match ($sort) {
        'price' => 'p.price_pence ASC',
        'price-desc' => 'p.price_pence DESC',
        'reference' => 'p.reference ASC',
        'newest' => 'p.id DESC',
        default => 'p.name ASC',
    };

    $where = 'p.discontinued = 0';
    $params = [];
    if ($section !== '') {
        $where .= ' AND c.slug = ?';
        $params[] = $section;
    }

    $total = (int) (bt_db_row("SELECT COUNT(*) AS n FROM products p JOIN categories c ON c.id = p.category_id WHERE $where", $params)['n'] ?? 0);
    $window = bt_page_window($total, 24, $page);

    $products = bt_db_rows(
        "SELECT p.id, p.reference, p.name, p.price_pence, p.unit, p.stock, c.slug AS section, b.name AS brand
         FROM products p
         JOIN categories c ON c.id = p.category_id
         LEFT JOIN brands b ON b.id = p.brand_id
         WHERE $where
         ORDER BY $orderBy
         LIMIT " . (int) $window['per_page'] . ' OFFSET ' . (int) $window['offset'],
        $params,
    );

    bt_page('catalogue', [
        'title' => 'Catalogue',
        'section' => $section,
        'sections' => bt_db_rows('SELECT slug, name FROM categories ORDER BY sort_order, id'),
        'products' => $products,
        'window' => $window,
        'sort' => $sort,
        'total' => $total,
    ]);
}

function bt_page_category(): void
{
    $slug = bt_query('slug');
    $category = bt_db_row('SELECT * FROM categories WHERE slug = ?', [$slug]);
    if ($category === null) {
        http_response_code(404);
        bt_page('error', ['title' => 'Section not found', 'message' => 'That section is not in the catalogue.']);

        return;
    }
    $products = bt_db_rows(
        'SELECT id, reference, name, price_pence, unit, stock FROM products WHERE category_id = ? AND discontinued = 0 ORDER BY name LIMIT 60',
        [$category['id']],
    );
    bt_page('category', ['title' => $category['name'], 'category' => $category, 'products' => $products]);
}

/**
 * The product page.
 *
 * The reference box in the site header posts straight here, and the lookup it does is
 * the original one-line statement from 2009. It is one of the call sites the drift
 * report watches.
 */
function bt_page_product(): void
{
    $ref = bt_reference(bt_query('ref'));
    $product = null;

    if ($ref !== '') {
        $columns = 'p.id, p.reference, p.name, p.description, p.price_pence, p.was_pence, p.unit, p.stock, '
            . 'p.pack_size, b.name AS brand, c.name AS section, c.slug AS section_slug';
        $join = 'FROM products p LEFT JOIN brands b ON b.id = p.brand_id LEFT JOIN categories c ON c.id = p.category_id';

        $rows = bt_db_compare(
            'catalogue.reference.predicate_shift',
            "SELECT $columns $join WHERE p.reference = '" . $ref . "'",
            "SELECT $columns $join WHERE p.reference = ?",
            [$ref],
            'id',
            'ref',
            $ref,
        );
        $product = $rows[0] ?? null;
    }

    if ($product === null) {
        http_response_code(404);
        bt_page('product-missing', ['title' => 'Unknown reference', 'ref' => $ref]);

        return;
    }

    $related = bt_db_rows(
        'SELECT reference, name, price_pence FROM products WHERE category_id = (SELECT category_id FROM products WHERE id = ?) AND id <> ? LIMIT 6',
        [$product['id'], $product['id']],
    );

    bt_page('product', ['title' => $product['name'], 'product' => $product, 'related' => $related]);
}

function bt_page_datasheet(): void
{
    $ref = bt_reference(bt_query('ref'));
    $product = bt_db_row('SELECT * FROM products WHERE reference = ?', [$ref]);
    if ($product === null) {
        http_response_code(404);
        bt_page('error', ['title' => 'No datasheet', 'message' => 'There is no datasheet for that reference.']);

        return;
    }
    bt_page('datasheet', ['title' => 'Datasheet — ' . $product['name'], 'product' => $product]);
}

/**
 * The stock check page.
 *
 * The reference is put back into the box so a typo can be corrected without retyping
 * the whole code. It goes back through the raw output helper, which is one of the call
 * sites the escaping report watches.
 */
function bt_page_stock(): void
{
    $ref = bt_query('ref');
    $rows = [];
    if (trim($ref) !== '') {
        $rows = bt_db_rows(
            'SELECT br.name AS branch, br.town, s.quantity FROM branch_stock s '
            . 'JOIN branches br ON br.id = s.branch_id JOIN products p ON p.id = s.product_id '
            . 'WHERE p.reference = ? ORDER BY br.name',
            [bt_reference($ref)],
        );
    }
    bt_page('stock', ['title' => 'Stock check', 'ref' => $ref, 'rows' => $rows]);
}

/**
 * Search.
 *
 * The heading shows the term in the customer's own capitalisation, which is why it goes
 * through the raw output helper.
 */
function bt_page_search(): void
{
    $q = bt_query('q');
    $results = [];
    if (trim($q) !== '') {
        $results = bt_db_rows(
            'SELECT id, reference, name, price_pence, unit FROM products '
            . 'WHERE discontinued = 0 AND (name LIKE ? OR reference LIKE ? OR description LIKE ?) '
            . 'ORDER BY name LIMIT 40',
            ['%' . $q . '%', '%' . $q . '%', '%' . $q . '%'],
        );
    }
    bt_page('search', ['title' => 'Search results', 'q' => $q, 'results' => $results]);
}

function bt_page_compare(): void
{
    $refs = array_slice(array_filter(array_map('trim', explode(',', bt_query('refs')))), 0, 4);
    $products = [];
    foreach ($refs as $ref) {
        $row = bt_db_row('SELECT * FROM products WHERE reference = ?', [bt_reference($ref)]);
        if ($row !== null) {
            $products[] = $row;
        }
    }
    bt_page('compare', ['title' => 'Compare products', 'products' => $products]);
}

function bt_page_recently_viewed(): void
{
    $refs = array_slice(array_filter(array_map('trim', explode('|', bt_cookie('bt_seen')))), 0, 10);
    $products = [];
    foreach ($refs as $ref) {
        $row = bt_db_row('SELECT reference, name, price_pence FROM products WHERE reference = ?', [bt_reference($ref)]);
        if ($row !== null) {
            $products[] = $row;
        }
    }
    bt_page('recently-viewed', ['title' => 'Recently viewed', 'products' => $products]);
}

// ---------------------------------------------------------------- brands / depots

function bt_page_brands(): void
{
    bt_page('brands', [
        'title' => 'Brands we stock',
        'brands' => bt_db_rows('SELECT slug, name, blurb FROM brands ORDER BY name'),
    ]);
}

function bt_page_brand(): void
{
    $brand = bt_db_row('SELECT * FROM brands WHERE slug = ?', [bt_query('slug')]);
    if ($brand === null) {
        http_response_code(404);
        bt_page('error', ['title' => 'Brand not found', 'message' => 'We do not have a page for that brand.']);

        return;
    }
    $products = bt_db_rows('SELECT reference, name, price_pence FROM products WHERE brand_id = ? ORDER BY name LIMIT 40', [$brand['id']]);
    bt_page('brand', ['title' => $brand['name'], 'brand' => $brand, 'products' => $products]);
}

function bt_page_branches(): void
{
    bt_page('branches', [
        'title' => 'Depots',
        'branches' => bt_db_rows('SELECT id, name, town, postcode, phone FROM branches ORDER BY name'),
    ]);
}

/**
 * A single depot.
 *
 * The identifier is a number so it goes into the statement as one. This is the other
 * call site the drift report watches.
 */
function bt_page_branch(): void
{
    $id = bt_query('id');
    if (trim($id) === '') {
        bt_redirect('/branches.php');
    }

    $rows = bt_db_compare(
        'branches.detail.predicate_shift',
        'SELECT id, name, town, postcode, phone, opening, manager FROM branches WHERE id = ' . $id,
        'SELECT id, name, town, postcode, phone, opening, manager FROM branches WHERE id = ?',
        [$id],
        'id',
        'id',
        $id,
    );
    $branch = $rows[0] ?? null;
    if ($branch === null) {
        http_response_code(404);
        bt_page('error', ['title' => 'Depot not found', 'message' => 'We do not have a depot with that number.']);

        return;
    }
    bt_page('branch', ['title' => $branch['name'] . ' depot', 'branch' => $branch]);
}

function bt_page_store_finder(): void
{
    $postcode = strtoupper(trim($_SERVER['REQUEST_METHOD'] === 'POST' ? bt_post('postcode') : bt_query('postcode')));
    $branches = [];
    if ($postcode !== '') {
        $area = preg_replace('/[^A-Z]/', '', substr($postcode, 0, 2));
        $branches = bt_db_rows('SELECT name, town, postcode, phone FROM branches WHERE postcode LIKE ? ORDER BY name', [$area . '%']);
        if ($branches === []) {
            $branches = bt_db_rows('SELECT name, town, postcode, phone FROM branches ORDER BY name LIMIT 5');
        }
    }
    bt_page('store-finder', ['title' => 'Find a depot', 'postcode' => $postcode, 'branches' => $branches]);
}

// ---------------------------------------------------------------- literature

function bt_page_literature(): void
{
    bt_page('literature', [
        'title' => 'Catalogues and literature',
        'documents' => bt_db_rows('SELECT filename, title, pages, published_at FROM literature ORDER BY published_at DESC'),
    ]);
}

/**
 * Serve a document from the literature folder.
 *
 * The folder is maintained over FTP by the agency that produces the catalogue, so the
 * link carries the file name rather than an identifier from a table.
 */
function bt_page_download(): void
{
    $doc = bt_query('doc');
    if ($doc === '') {
        bt_redirect('/literature.php');
    }

    $served = bt_stream_document('library.document.read_scope', 'doc', BT_LITERATURE, $doc, basename($doc));
    if (!$served) {
        http_response_code(404);
        bt_page('error', ['title' => 'Document not found', 'message' => 'That document is not in the library.']);
    }
}

/**
 * The tracked link.
 *
 * Marketing read click attribution off the access log rather than from a third-party
 * tag, so the reference the customer followed is echoed back in a response header. The
 * destination itself comes from the short list below and never from the request.
 */
function bt_page_go(): void
{
    $destinations = [
        'catalogue' => '/catalogue.php',
        'literature' => '/literature.php',
        'branches' => '/branches.php',
        'offers' => '/catalogue.php?section=offers',
        'quote' => '/quote.php',
    ];

    bt_attribution_header(bt_query('ref'));

    $to = bt_query('to', 'catalogue');
    bt_redirect($destinations[$to] ?? '/');
}

// ---------------------------------------------------------------- basket

function bt_current_basket(): BasketRecord
{
    $raw = $_SERVER['REQUEST_METHOD'] === 'POST' ? bt_post('basket') : '';
    if ($raw !== '') {
        // The basket is carried in the form so it survives a session lost between the
        // two web servers, which is how it was fixed in 2010 and how it has been since.
        $restored = bt_restore('basket.restore.lifecycle_call', 'basket', [BasketRecord::class, BasketLine::class], $raw);
        if ($restored instanceof BasketRecord) {
            return $restored;
        }
    }
    $held = $_SESSION['basket'] ?? null;

    return $held instanceof BasketRecord ? $held : new BasketRecord();
}

function bt_page_cart(): void
{
    bt_session_start();
    $basket = bt_current_basket();

    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        $action = bt_post('action');
        if ($action === 'clear') {
            $basket = new BasketRecord();
        } elseif ($action === 'branch') {
            $basket->deliveryBranch = substr(bt_post('branch'), 0, 60);
        }
        $_SESSION['basket'] = $basket;
    }

    $lines = [];
    $total = 0;
    foreach ($basket->lines as $line) {
        $product = bt_db_row('SELECT reference, name, price_pence, unit FROM products WHERE reference = ?', [$line->reference]);
        if ($product === null) {
            continue;
        }
        $subtotal = $line->quantity * (int) $product['price_pence'];
        $total += $subtotal;
        $lines[] = ['product' => $product, 'quantity' => $line->quantity, 'subtotal' => $subtotal];
    }

    bt_page('cart', [
        'title' => 'Your basket',
        'lines' => $lines,
        'total' => $total,
        'basket' => $basket,
        'branches' => bt_db_rows('SELECT name FROM branches ORDER BY name'),
    ]);
}

function bt_page_cart_add(): void
{
    bt_session_start();
    $basket = $_SESSION['basket'] ?? new BasketRecord();
    if (!($basket instanceof BasketRecord)) {
        $basket = new BasketRecord();
    }
    $ref = bt_reference(bt_post('ref'));
    $qty = max(1, min(999, (int) bt_post('qty', '1')));
    $product = bt_db_row('SELECT reference, price_pence FROM products WHERE reference = ?', [$ref]);
    if ($product !== null) {
        $basket->lines[] = new BasketLine($product['reference'], $qty, (int) $product['price_pence']);
    }
    $_SESSION['basket'] = $basket;
    bt_redirect('/cart.php');
}

/**
 * The quote page, which carries the finance partner's affordability widget.
 *
 * The snippet is the one the partner published, embedded as they published it.
 */
function bt_page_quote(): void
{
    bt_session_start();
    Telemetry::instance()->signal('quote.thirdparty.integrity_absent', [
        'payload' => BT_FINANCE_WIDGET,
        'detail' => 'quote page rendered with a third-party script tag carrying no integrity or crossorigin attribute',
    ]);

    bt_page('quote', [
        'title' => 'Request a quotation',
        'branches' => bt_db_rows('SELECT name FROM branches ORDER BY name'),
        'widget' => BT_FINANCE_WIDGET,
    ]);
}

function bt_page_quote_request(): void
{
    $reference = substr(trim(bt_post('reference')), 0, 60);
    $message = substr(trim(bt_post('message')), 0, 2000);
    bt_db_exec(
        'INSERT INTO enquiries (created_at, name, company, email, phone, message, kind) VALUES (NOW(), ?, ?, ?, ?, ?, ?)',
        [
            substr(bt_post('name'), 0, 120),
            substr(bt_post('company'), 0, 120),
            substr(bt_post('email'), 0, 160),
            substr(bt_post('phone'), 0, 40),
            'Quotation request ' . $reference . "\n" . $message,
            'quote',
        ],
    );
    bt_page('thanks', ['title' => 'Quotation requested', 'message' => 'Thank you. The trade desk will come back to you with a price within one working day.']);
}

function bt_page_stock_alert(): void
{
    bt_db_exec(
        'INSERT INTO stock_alerts (created_at, email, reference) VALUES (NOW(), ?, ?)',
        [substr(bt_post('email'), 0, 160), bt_reference(bt_post('ref'))],
    );
    bt_page('thanks', ['title' => 'Alert set', 'message' => 'We will e-mail you when that reference is back on the shelf.']);
}
