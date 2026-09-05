<?php
/**
 * The page furniture: masthead, navigation, the page itself, and the footer.
 *
 * The masthead comes from the appearance folder rather than from this file, because the
 * depot terminals were given a heavier one when the counters were refitted. The name of
 * the fragment is carried in a cookie.
 */

$bt_skin = bt_cookie('bt_skin', 'slate.php');
$bt_year = date('Y');
?><!DOCTYPE html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title><?= bt_e((string) $bt_title) ?> — <?= bt_e(bt_site_name()) ?></title>
<meta name="description" content="Trade tools, fixings and plant consumables. Eight depots, next-day delivery on stock lines.">
<link rel="stylesheet" href="/assets/style.css">
<link rel="icon" href="/assets/favicon.ico">
</head>
<body>

<?php if (!bt_include_from('layout.skin.include_scope', 'bt_skin', BT_SKINS, $bt_skin)) { ?>
<div class="masthead">
  <a class="brand" href="/"><?= bt_e(bt_site_name()) ?></a>
  <span class="strap">Fixings, tools and plant consumables since 1962</span>
</div>
<?php } ?>

<div class="topbar">
  <form class="reflookup" method="get" action="/product.php">
    <label for="ref">Quick reference</label>
    <input type="text" id="ref" name="ref" value="" size="12" placeholder="BT-0000">
    <button type="submit">Go</button>
  </form>
  <form class="sitesearch" method="get" action="/search.php">
    <label for="q">Search</label>
    <input type="text" id="q" name="q" value="" size="24">
    <button type="submit">Search</button>
  </form>
  <span class="phone">Trade desk 01422 000000</span>
</div>

<ul class="nav">
  <li><a href="/catalogue.php">Catalogue</a></li>
  <li><a href="/brands.php">Brands</a></li>
  <li><a href="/services.php">Services</a></li>
  <li><a href="/literature.php">Literature</a></li>
  <li><a href="/branches.php">Depots</a></li>
  <li><a href="/delivery.php">Delivery</a></li>
  <li><a href="/contact.php">Contact</a></li>
  <li><a href="/cart.php">Basket</a></li>
  <li><a href="/account/index.php">Your account</a></li>
</ul>

<div class="page">
<h1><?= bt_e((string) $bt_title) ?></h1>
<?php require BT_TEMPLATES . '/' . basename((string) $bt_template) . '.php'; ?>
</div>

<div class="footer">
  <ul>
    <li><a href="/about.php">About us</a></li>
    <li><a href="/history.php">Our history</a></li>
    <li><a href="/careers.php">Careers</a></li>
    <li><a href="/terms.php">Conditions of sale</a></li>
    <li><a href="/privacy.php">Privacy</a></li>
    <li><a href="/cookies.php">Cookies</a></li>
    <li><a href="/accessibility.php">Accessibility</a></li>
    <li><a href="/returns.php">Returns</a></li>
    <li><a href="/credit-account.php">Credit accounts</a></li>
    <li><a href="/trade-account.php">Trade accounts</a></li>
    <li><a href="/hire-terms.php">Hire terms</a></li>
    <li><a href="/health-and-safety.php">Health and safety</a></li>
    <li><a href="/accreditations.php">Accreditations</a></li>
    <li><a href="/sustainability.php">Sustainability</a></li>
    <li><a href="/suppliers.php">Suppliers</a></li>
    <li><a href="/price-promise.php">Price promise</a></li>
    <li><a href="/price-list.php">Price list</a></li>
    <li><a href="/quality.php">Quality</a></li>
    <li><a href="/environmental.php">Environment</a></li>
    <li><a href="/modern-slavery.php">Modern slavery statement</a></li>
    <li><a href="/insurance.php">Insurance</a></li>
    <li><a href="/training.php">Training</a></li>
    <li><a href="/faq.php">Questions</a></li>
    <li><a href="/help.php">Help</a></li>
    <li><a href="/sitemap.php">Site map</a></li>
    <li><a href="/news.php">News</a></li>
    <li><a href="/vacancies.php">Vacancies</a></li>
    <li><a href="/feedback.php">Feedback</a></li>
    <li><a href="/newsletter.php">Newsletter</a></li>
    <li><a href="/callback.php">Call me back</a></li>
    <li><a href="/store-finder.php">Find a depot</a></li>
    <li><a href="/recently-viewed.php">Recently viewed</a></li>
  </ul>
  <p class="small">
    <?= bt_e(bt_site_name()) ?> Limited, Lowfields Way, Elland, West Yorkshire.
    Registered in England 00874112. VAT 411 8823 06.
    &copy; <?= bt_e($bt_year) ?>.
  </p>
  <p class="small cookie-note" id="cookie-note">
    This site keeps a small number of cookies so the basket and your sign-in work.
    <a href="/cookies.php">More about cookies</a>.
    <button type="button" onclick="btDismissCookieNote()">That is fine</button>
  </p>
</div>

<script src="/assets/site.js"></script>
<script src="/assets/counters.js"></script>
</body>
</html>
