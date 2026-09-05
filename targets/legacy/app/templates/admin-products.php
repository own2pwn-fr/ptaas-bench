<ul class="crumbs">
  <li><a href="/admin/index.php">Console</a></li>
  <li><a href="/admin/orders.php">Orders</a></li>
  <li><a href="/admin/customers.php">Customers</a></li>
  <li><a href="/admin/products.php">Products</a></li>
  <li><a href="/admin/enquiries.php">Enquiries</a></li>
  <li><a href="/admin/branches.php">Depots</a></li>
  <li><a href="/admin/reports.php">Reports</a></li>
  <li><a href="/admin/tools.php">Storage</a></li>
  <li><a href="/admin/settings.php">Settings</a></li>
  <li><a href="/admin/audit.php">Activity</a></li>
</ul>

<p>
  Catalogue lines in reference order. The box matches on either the reference or the
  description, so part of a word is enough.
</p>

<form method="get" action="/admin/products.php" class="inline">
  <label for="q">Find</label>
  <input type="text" id="q" name="q" size="24" value="<?= bt_e((string) $q) ?>">
  <button type="submit">Search</button>
</form>

<?php if ($products === []) { ?>
  <p>Nothing matched. Try a shorter word, or the reference off the printed catalogue.</p>
<?php } else { ?>
  <p class="small"><?= count($products) ?> line(s) listed.</p>
  <table class="grid">
    <thead>
      <tr><th>Reference</th><th>Description</th><th class="num">Price</th><th class="num">Stock</th><th>Listed</th></tr>
    </thead>
    <tbody>
    <?php foreach ($products as $product) { ?>
      <tr>
        <td><a href="/admin/product.php?ref=<?= bt_e(rawurlencode((string) ($product['reference'] ?? ''))) ?>"><?= bt_e((string) ($product['reference'] ?? '')) ?></a></td>
        <td><?= bt_e((string) ($product['name'] ?? '')) ?></td>
        <td class="num"><?= bt_e(bt_money((int) ($product['price_pence'] ?? 0))) ?></td>
        <td class="num"><?= bt_e((string) (int) ($product['stock'] ?? 0)) ?></td>
        <td><?= (int) ($product['discontinued'] ?? 0) === 1 ? 'Withdrawn' : 'Yes' ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
<?php } ?>
