<p>
  The last few lines you looked at on this machine. The list is kept in a cookie on your
  own computer, so it will look different at the counter terminal or on a colleague's
  machine, and it clears when the cookie does.
</p>

<?php if ($products === []) { ?>
  <p>Nothing has been looked at yet. Start with <a href="/catalogue.php">the catalogue</a>.</p>
<?php } else { ?>
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
  <p class="small"><a href="/compare.php">Put two of them side by side</a></p>
<?php } ?>
