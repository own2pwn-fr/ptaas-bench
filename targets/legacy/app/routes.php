<?php
/**
 * The route table.
 *
 * The URLs are the ones the site has always had -- customers have them bookmarked and
 * the printed catalogue has them on the back page -- so what is registered here is a
 * list of .php paths, even though nothing is served from a file of that name any more.
 *
 * The fourth argument is what the front controller does before the page runs:
 *   public   nothing
 *   session  start a session, because the page keeps a basket
 *   account  start a session and require a signed-in contact
 *   staff    start a session and require a Braithwaite sign-in
 */

declare(strict_types=1);

function bt_register_routes(BtRouter $router): void
{
    // ------------------------------------------------------------- catalogue
    $router->get('/', 'bt_page_home');
    $router->get('/index.php', 'bt_page_home');
    $router->get('/catalogue.php', 'bt_page_catalogue');
    $router->get('/category.php', 'bt_page_category');
    $router->get('/product.php', 'bt_page_product');
    $router->get('/product-datasheet.php', 'bt_page_datasheet');
    $router->get('/stock.php', 'bt_page_stock');
    $router->get('/search.php', 'bt_page_search');
    $router->get('/compare.php', 'bt_page_compare');
    $router->get('/recently-viewed.php', 'bt_page_recently_viewed');
    $router->get('/brands.php', 'bt_page_brands');
    $router->get('/brand.php', 'bt_page_brand');
    $router->get('/branches.php', 'bt_page_branches');
    $router->get('/branch.php', 'bt_page_branch');
    $router->form('/store-finder.php', 'bt_page_store_finder');
    $router->get('/literature.php', 'bt_page_literature');
    $router->get('/download.php', 'bt_page_download');
    $router->get('/go.php', 'bt_page_go');

    // ----------------------------------------------------------------- basket
    $router->get('/cart.php', 'bt_page_cart', 'session');
    $router->post('/cart.php', 'bt_page_cart', 'session');
    $router->post('/cart-add.php', 'bt_page_cart_add', 'session');
    $router->get('/quote.php', 'bt_page_quote', 'session');
    $router->post('/quote-request.php', 'bt_page_quote_request');
    $router->post('/stock-alert.php', 'bt_page_stock_alert');

    // ------------------------------------------------------------------ forms
    $router->form('/contact.php', 'bt_page_contact');
    $router->form('/newsletter.php', 'bt_page_newsletter');
    $router->form('/callback.php', 'bt_page_callback');
    $router->form('/feedback.php', 'bt_page_feedback');
    $router->form('/careers-apply.php', 'bt_page_careers_apply');

    // ------------------------------------------------------------------- text
    $router->get('/help.php', 'bt_page_help');
    $router->get('/faq.php', 'bt_page_faq');
    $router->get('/sitemap.php', 'bt_page_sitemap_page');
    $router->get('/news.php', 'bt_page_news');
    $router->get('/news-item.php', 'bt_page_news_item');
    $router->get('/vacancies.php', 'bt_page_vacancies');
    $router->get('/vacancy.php', 'bt_page_vacancy');

    foreach (array_keys(bt_content_pages()) as $slug) {
        $router->get('/' . $slug . '.php', static function () use ($slug): void {
            bt_page_content($slug);
        });
    }

    // --------------------------------------------------------- the small files
    $router->get('/robots.txt', 'bt_page_robots');
    $router->get('/sitemap.xml', 'bt_page_sitemap_xml');
    $router->get('/.well-known/security.txt', 'bt_page_security_txt');
    $router->get('/health.php', 'bt_page_health');

    // ---------------------------------------------------------------- account
    $router->form('/account/login.php', 'bt_page_login');
    $router->get('/account/logout.php', 'bt_page_logout');
    $router->form('/account/reset.php', 'bt_page_reset');
    $router->form('/account/register.php', 'bt_page_register');

    $router->get('/account/index.php', 'bt_page_account_home', 'account');
    $router->get('/account/orders.php', 'bt_page_orders', 'account');
    $router->get('/account/order.php', 'bt_page_order', 'account');
    $router->get('/account/statements.php', 'bt_page_statements', 'account');
    $router->get('/account/invoice.php', 'bt_page_invoice', 'account');
    $router->get('/account/documents.php', 'bt_page_documents', 'account');
    $router->post('/account/documents.php', 'bt_page_documents', 'account');
    $router->get('/account/profile.php', 'bt_page_profile', 'account');
    $router->form('/account/quote.php', 'bt_page_account_quote', 'account');
    $router->form('/account/addresses.php', 'bt_page_addresses', 'account');
    $router->get('/account/address.php', 'bt_page_address', 'account');
    $router->get('/account/contacts.php', 'bt_page_contacts', 'account');
    $router->get('/account/users.php', 'bt_page_users', 'account');
    $router->form('/account/preferences.php', 'bt_page_preferences', 'account');
    $router->form('/account/password.php', 'bt_page_password', 'account');
    $router->form('/account/favourites.php', 'bt_page_favourites', 'account');
    $router->get('/account/templates.php', 'bt_page_templates', 'account');
    $router->get('/account/deliveries.php', 'bt_page_deliveries', 'account');

    // ---------------------------------------------------------------- console
    $router->get('/admin/index.php', 'bt_page_admin_home', 'staff');
    $router->get('/admin/widget.php', 'bt_page_admin_widget', 'staff');
    $router->form('/admin/tools.php', 'bt_page_admin_tools', 'staff');
    $router->form('/admin/reports.php', 'bt_page_admin_reports', 'staff');
    $router->get('/admin/enquiries.php', 'bt_page_admin_enquiries', 'staff');
    $router->get('/admin/customers.php', 'bt_page_admin_customers', 'staff');
    $router->get('/admin/customer.php', 'bt_page_admin_customer', 'staff');
    $router->get('/admin/orders.php', 'bt_page_admin_orders', 'staff');
    $router->get('/admin/order.php', 'bt_page_admin_order', 'staff');
    $router->get('/admin/products.php', 'bt_page_admin_products', 'staff');
    $router->form('/admin/product.php', 'bt_page_admin_product', 'staff');
    $router->form('/admin/settings.php', 'bt_page_admin_settings', 'staff');
    $router->get('/admin/audit.php', 'bt_page_admin_audit', 'staff');
    $router->get('/admin/branches.php', 'bt_page_admin_branches', 'staff');
}
