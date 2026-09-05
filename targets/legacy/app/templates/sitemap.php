<p>
  Everything on the site in one list. There is also a
  <a href="/sitemap.xml">machine-readable version</a> for search engines.
</p>

<div class="two-column">
  <div class="main">
    <h2>Catalogue sections</h2>
    <?php if ($sections === []) { ?>
      <p class="small">No sections are listed at the moment.</p>
    <?php } else { ?>
      <ul class="plain">
      <?php foreach ($sections as $section) { ?>
        <li><a href="/category.php?slug=<?= bt_e(rawurlencode((string) ($section['slug'] ?? ''))) ?>"><?= bt_e((string) ($section['name'] ?? '')) ?></a></li>
      <?php } ?>
      </ul>
    <?php } ?>

    <h2>About the company</h2>
    <?php if ($pages === []) { ?>
      <p class="small">No pages are listed at the moment.</p>
    <?php } else { ?>
      <ul class="plain">
      <?php foreach ($pages as $page) { ?>
        <li><a href="/<?= bt_e(rawurlencode((string) ($page['slug'] ?? ''))) ?>.php"><?= bt_e((string) ($page['title'] ?? '')) ?></a></li>
      <?php } ?>
      </ul>
    <?php } ?>
  </div>

  <div class="side">
    <h2>Ordering</h2>
    <ul class="plain">
      <li><a href="/catalogue.php">The catalogue</a></li>
      <li><a href="/search.php">Search</a></li>
      <li><a href="/brands.php">Brands</a></li>
      <li><a href="/stock.php">Stock check</a></li>
      <li><a href="/compare.php">Compare products</a></li>
      <li><a href="/recently-viewed.php">Recently viewed</a></li>
      <li><a href="/cart.php">Your basket</a></li>
      <li><a href="/quote.php">Request a quotation</a></li>
    </ul>

    <h2>Depots and delivery</h2>
    <ul class="plain">
      <li><a href="/branches.php">Depots</a></li>
      <li><a href="/store-finder.php">Find a depot</a></li>
      <li><a href="/delivery.php">Delivery</a></li>
      <li><a href="/services.php">Services</a></li>
      <li><a href="/literature.php">Catalogues and literature</a></li>
    </ul>

    <h2>Getting in touch</h2>
    <ul class="plain">
      <li><a href="/contact.php">Contact us</a></li>
      <li><a href="/callback.php">Request a call back</a></li>
      <li><a href="/feedback.php">Feedback</a></li>
      <li><a href="/newsletter.php">Trade newsletter</a></li>
      <li><a href="/faq.php">Frequently asked questions</a></li>
      <li><a href="/help.php">Help</a></li>
    </ul>

    <h2>News and jobs</h2>
    <ul class="plain">
      <li><a href="/news.php">News</a></li>
      <li><a href="/vacancies.php">Current vacancies</a></li>
      <li><a href="/careers-apply.php">Apply for a vacancy</a></li>
    </ul>

    <h2>Your account</h2>
    <ul class="plain">
      <li><a href="/account/index.php">Account home</a></li>
      <li><a href="/account/login.php">Sign in</a></li>
      <li><a href="/account/register.php">Apply for a trade account</a></li>
    </ul>
  </div>
</div>
