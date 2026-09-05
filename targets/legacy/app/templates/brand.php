<p class="crumbs">
  <a href="/">Home</a> &rsaquo; <a href="/brands.php">Brands</a> &rsaquo;
  <?= bt_e((string) ($brand['name'] ?? '')) ?>
</p>

<?php if (trim((string) ($brand['blurb'] ?? '')) !== '') { ?>
  <p><?= bt_e((string) ($brand['blurb'] ?? '')) ?></p>
<?php } ?>

<?php if ($products === []) { ?>
  <p>
    We hold no stock lines against this make at present, though the buying office can
    normally get one in. Ring the trade desk on 01422 000000.
  </p>
<?php } else { ?>
  <h2>Stock lines</h2>
  <table class="grid">
    <thead><tr><th>Reference</th><th>Description</th><th class="num">Price</th></tr></thead>
    <tbody>
    <?php foreach ($products as $product) { ?>
      <tr>
        <td><a href="/product.php?ref=<?= bt_e(rawurlencode((string) ($product['reference'] ?? ''))) ?>"><?= bt_e((string) ($product['reference'] ?? '')) ?></a></td>
        <td><?= bt_e((string) ($product['name'] ?? '')) ?></td>
        <td class="num"><?= bt_e(bt_money((int) ($product['price_pence'] ?? 0))) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
  <p class="small">The first forty lines by description. The rest are in <a href="/catalogue.php">the catalogue</a>.</p>
<?php } ?>
