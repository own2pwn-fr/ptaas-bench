<p>
  Fixings, hand tools, power tool accessories and plant consumables, held in stock at
  Elland and at eight depots across the north of England. Anything stocked and ordered
  before four o'clock on a working day goes out the same evening. Account customers see
  their own agreed prices once they have signed in.
</p>

<p class="small">
  Your listing preference is set to <?= bt_e((string) ($prefs->listing ?? 'grid')) ?>,
  <?= bt_e((string) ((int) ($prefs->perPage ?? 24))) ?> lines to a page.
  <a href="/account/preferences.php">Change how listings look</a>.
</p>

<h2>This month's offers</h2>

<?php if ($offers === []) { ?>
  <p>There is nothing on offer this month. The counters will still price a quantity.</p>
<?php } elseif ((string) ($prefs->listing ?? 'grid') === 'list') { ?>
  <table class="grid">
    <thead><tr><th>Reference</th><th>Description</th><th class="num">Price</th><th class="num">Was</th></tr></thead>
    <tbody>
    <?php foreach ($offers as $offer) { ?>
      <tr>
        <td><a href="/product.php?ref=<?= bt_e(rawurlencode((string) ($offer['reference'] ?? ''))) ?>"><?= bt_e((string) ($offer['reference'] ?? '')) ?></a></td>
        <td><?= bt_e((string) ($offer['name'] ?? '')) ?></td>
        <td class="num"><?= bt_e(bt_money((int) ($offer['price_pence'] ?? 0))) ?></td>
        <td class="num"><?php if ((int) ($offer['was_pence'] ?? 0) > (int) ($offer['price_pence'] ?? 0)) { ?><?= bt_e(bt_money((int) ($offer['was_pence'] ?? 0))) ?><?php } ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
<?php } else { ?>
  <ul class="tiles">
  <?php foreach ($offers as $offer) { ?>
    <li>
      <a href="/product.php?ref=<?= bt_e(rawurlencode((string) ($offer['reference'] ?? ''))) ?>"><?= bt_e((string) ($offer['name'] ?? '')) ?></a>
      <span class="small"><?= bt_e((string) ($offer['reference'] ?? '')) ?></span>
      <span class="num"><?= bt_e(bt_money((int) ($offer['price_pence'] ?? 0))) ?></span>
      <?php if ((int) ($offer['was_pence'] ?? 0) > (int) ($offer['price_pence'] ?? 0)) { ?>
        <span class="small">was <?= bt_e(bt_money((int) ($offer['was_pence'] ?? 0))) ?></span>
      <?php } ?>
    </li>
  <?php } ?>
  </ul>
<?php } ?>

<h2>Round the catalogue</h2>

<?php if ($sections === []) { ?>
  <p>The catalogue sections are being reworked. Use the search box at the top of the page.</p>
<?php } else { ?>
  <ul class="tiles">
  <?php foreach ($sections as $section) { ?>
    <li>
      <a href="/category.php?slug=<?= bt_e(rawurlencode((string) ($section['slug'] ?? ''))) ?>"><?= bt_e((string) ($section['name'] ?? '')) ?></a>
      <span class="small"><?= bt_e((string) ($section['blurb'] ?? '')) ?></span>
    </li>
  <?php } ?>
  </ul>
  <p class="small"><a href="/catalogue.php">The whole catalogue</a></p>
<?php } ?>

<h2>News from the depots</h2>

<?php if ($news === []) { ?>
  <p>Nothing new this month.</p>
<?php } else { ?>
  <ul class="plain">
  <?php foreach ($news as $item) { ?>
    <li>
      <a href="/news-item.php?slug=<?= bt_e(rawurlencode((string) ($item['slug'] ?? ''))) ?>"><?= bt_e((string) ($item['title'] ?? '')) ?></a>
      <span class="small"><?= bt_e(bt_date((string) ($item['published_at'] ?? ''))) ?></span>
      <p><?= bt_e((string) ($item['summary'] ?? '')) ?></p>
    </li>
  <?php } ?>
  </ul>
  <p class="small"><a href="/news.php">All our news</a></p>
<?php } ?>
