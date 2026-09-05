<p class="crumbs">
  <a href="/">Home</a> &rsaquo; <a href="/catalogue.php">Catalogue</a> &rsaquo;
  <?= bt_e((string) ($category['name'] ?? '')) ?>
</p>

<?php if (trim((string) ($category['blurb'] ?? '')) !== '') { ?>
  <p><?= bt_e((string) ($category['blurb'] ?? '')) ?></p>
<?php } ?>

<?php if ($products === []) { ?>
  <p>Nothing is listed in this section at the moment. The counters can still get most of it in for the next day; ring the trade desk on 01422 000000.</p>
<?php } else { ?>
  <table class="grid">
    <thead><tr><th>Reference</th><th>Description</th><th class="num">Price</th><th>Unit</th><th class="num">Stock</th></tr></thead>
    <tbody>
    <?php foreach ($products as $product) { ?>
      <tr>
        <td><a href="/product.php?ref=<?= bt_e(rawurlencode((string) ($product['reference'] ?? ''))) ?>"><?= bt_e((string) ($product['reference'] ?? '')) ?></a></td>
        <td><?= bt_e((string) ($product['name'] ?? '')) ?></td>
        <td class="num"><?= bt_e(bt_money((int) ($product['price_pence'] ?? 0))) ?></td>
        <td><?= bt_e((string) ($product['unit'] ?? '')) ?></td>
        <td class="num"><?= (int) ($product['stock'] ?? 0) > 0 ? bt_e((string) (int) ($product['stock'] ?? 0)) : 'to order' ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
  <p class="small">
    Showing up to sixty lines. The rest of the section is in
    <a href="/catalogue.php?section=<?= bt_e(rawurlencode((string) ($category['slug'] ?? ''))) ?>">the full catalogue listing</a>.
  </p>
<?php } ?>
