<?php
/**
 * The pages that are text: the brochure pages, the help articles, and the small
 * machine-readable files at the root of the site.
 *
 * The brochure copy itself lives in copy.php, which is where the content was moved when
 * it was taken out of the templates.
 */

declare(strict_types=1);

require_once __DIR__ . '/copy.php';

function bt_page_content(string $slug): void
{
    $pages = bt_content_pages();
    $page = $pages[$slug] ?? null;
    if ($page === null) {
        http_response_code(404);
        bt_page('error', ['title' => 'Page not found', 'message' => 'That page is no longer on the site.']);

        return;
    }
    bt_page('content', [
        'title' => $page['title'],
        'strapline' => $page['strapline'],
        'sections' => $page['sections'],
    ]);
}

/**
 * A help article.
 *
 * Articles are partial templates on disk, added by whoever writes them, so the footer
 * links pass the file name. This is one of the call sites the fragment report watches.
 */
function bt_page_help(): void
{
    $topic = bt_query('topic', 'delivery-times.php');

    bt_page('help', [
        'title' => 'Help',
        'topic' => $topic,
        'articles' => bt_help_index(),
    ]);
}

/** @return list<array{0:string,1:string}> */
function bt_help_index(): array
{
    return [
        ['delivery-times.php', 'Delivery times and carriage'],
        ['returns-process.php', 'Sending something back'],
        ['account-application.php', 'Applying for an account'],
        ['ordering-by-phone.php', 'Ordering by telephone'],
        ['reference-codes.php', 'Understanding a reference code'],
        ['pallet-deliveries.php', 'Pallet deliveries'],
        ['collection-from-depot.php', 'Collecting from a depot'],
        ['statements-and-invoices.php', 'Statements and invoices'],
    ];
}

function bt_page_faq(): void
{
    bt_page('faq', [
        'title' => 'Frequently asked questions',
        'items' => [
            ['Do I need an account to order?', 'No. Anyone can order from the trade counter or on the telephone. An account gives you 30-day terms and your own price list.'],
            ['What time do I need to order for next day?', 'Four o\'clock, on any stocked line, Monday to Thursday. Friday orders go out on Monday.'],
            ['Can I collect?', 'Yes, from any of the eight depots. Order online and choose the depot; you will get a message when it is picked.'],
            ['Do you deliver to site?', 'We do, including to sites with a delivery window. Tell the trade desk when you place the order.'],
            ['Do you sell to the public?', 'The counters are open to anyone. Prices at the counter are the list prices.'],
            ['How do I get a copy of the printed catalogue?', 'Ask at any counter or use the enquiry form. A new one is issued each January.'],
            ['Can you cut studding to length?', 'Yes, at every depot, usually while you wait. There is a page about it under Services.'],
            ['What are your payment terms?', 'Thirty days from statement for account customers. Everyone else pays at the point of order.'],
        ],
    ]);
}

function bt_page_sitemap_page(): void
{
    bt_page('sitemap', [
        'title' => 'Site map',
        'sections' => bt_db_rows('SELECT slug, name FROM categories ORDER BY sort_order, id'),
        'pages' => array_map(
            static fn (string $slug, array $page): array => ['slug' => $slug, 'title' => $page['title']],
            array_keys(bt_content_pages()),
            array_values(bt_content_pages()),
        ),
    ]);
}

function bt_page_news(): void
{
    bt_page('news', [
        'title' => 'News',
        'items' => bt_db_rows('SELECT slug, title, published_at, summary FROM news ORDER BY published_at DESC LIMIT 20'),
    ]);
}

function bt_page_news_item(): void
{
    $item = bt_db_row('SELECT * FROM news WHERE slug = ?', [bt_query('slug')]);
    if ($item === null) {
        http_response_code(404);
        bt_page('error', ['title' => 'Not found', 'message' => 'That news item is not on the site.']);

        return;
    }
    bt_page('news-item', ['title' => $item['title'], 'item' => $item]);
}

function bt_page_vacancies(): void
{
    bt_page('vacancies', [
        'title' => 'Current vacancies',
        'items' => bt_db_rows('SELECT slug, title, location, closes_at FROM vacancies ORDER BY closes_at'),
    ]);
}

function bt_page_vacancy(): void
{
    $item = bt_db_row('SELECT * FROM vacancies WHERE slug = ?', [bt_query('slug')]);
    if ($item === null) {
        http_response_code(404);
        bt_page('error', ['title' => 'Not found', 'message' => 'That vacancy has closed.']);

        return;
    }
    bt_page('vacancy', ['title' => $item['title'], 'item' => $item]);
}

// ---------------------------------------------------- machine-readable odds and ends

function bt_page_robots(): void
{
    header('Content-Type: text/plain; charset=utf-8');
    echo "User-agent: *\n";
    echo "Disallow: /account/\n";
    echo "Disallow: /admin/\n";
    echo "Disallow: /cart.php\n";
    echo "Disallow: /go.php\n";
    echo "Allow: /\n";
    echo "\n";
    echo 'Sitemap: http://www.' . bt_site_domain() . "/sitemap.xml\n";
}

function bt_page_sitemap_xml(): void
{
    header('Content-Type: application/xml; charset=utf-8');
    $base = 'http://www.' . bt_site_domain();
    $paths = ['/', '/catalogue.php', '/brands.php', '/branches.php', '/literature.php', '/news.php',
              '/vacancies.php', '/faq.php', '/help.php', '/contact.php', '/quote.php', '/sitemap.php'];
    foreach (array_keys(bt_content_pages()) as $slug) {
        $paths[] = '/' . $slug . '.php';
    }
    foreach (bt_db_rows('SELECT slug FROM categories ORDER BY sort_order, id') as $row) {
        $paths[] = '/category.php?slug=' . rawurlencode((string) $row['slug']);
    }

    echo '<?xml version="1.0" encoding="UTF-8"?>' . "\n";
    echo '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' . "\n";
    foreach ($paths as $path) {
        echo '  <url><loc>' . bt_e($base . $path) . '</loc></url>' . "\n";
    }
    echo "</urlset>\n";
}

function bt_page_security_txt(): void
{
    header('Content-Type: text/plain; charset=utf-8');
    echo "Contact: mailto:it@" . bt_site_domain() . "\n";
    echo "Contact: tel:+44-1422-000000\n";
    echo "Preferred-Languages: en\n";
    echo 'Canonical: http://www.' . bt_site_domain() . "/.well-known/security.txt\n";
    echo "Expires: 2027-01-01T00:00:00.000Z\n";
}

function bt_page_health(): void
{
    header('Content-Type: text/plain; charset=utf-8');
    try {
        bt_db_row('SELECT 1 AS ok');
        echo "ok\n";
    } catch (Throwable) {
        http_response_code(503);
        echo "degraded\n";
    }
}
