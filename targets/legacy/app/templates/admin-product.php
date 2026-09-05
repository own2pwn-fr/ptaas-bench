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

<?php if ($saved) { ?>
  <p class="notice">Saved. The counters will pick the new figures up on their next refresh.</p>
<?php } ?>

<p class="small"><a href="/admin/products.php">Back to the catalogue list</a></p>

<table class="grid">
  <tbody>
    <tr><th>Reference</th><td><?= bt_e((string) ($product['reference'] ?? '')) ?></td></tr>
    <tr><th>Description</th><td><?= bt_e((string) ($product['name'] ?? '')) ?></td></tr>
    <tr><th>Unit</th><td><?= bt_e((string) ($product['unit'] ?? '')) ?></td></tr>
    <tr><th>Pack size</th><td><?= bt_e((string) ($product['pack_size'] ?? '')) ?></td></tr>
    <tr><th>Listed</th><td><?= (int) ($product['discontinued'] ?? 0) === 1 ? 'Withdrawn' : 'Yes' ?></td></tr>
  </tbody>
</table>

<?php if (trim((string) ($product['description'] ?? '')) !== '') { ?>
  <p><?= bt_e((string) ($product['description'] ?? '')) ?></p>
<?php } ?>

<h2>Amend</h2>

<p class="small">
  Price is in pounds and pence, as it is printed. Stock is the figure for Elland; depot
  stock comes off the picking rounds and is not edited here.
</p>

<form method="post" action="/admin/product.php" class="stacked">
  <input type="hidden" name="reference" value="<?= bt_e((string) ($product['reference'] ?? '')) ?>">
  <p>
    <label for="price">Price</label>
    <input type="text" id="price" name="price" size="10" value="<?= bt_e(number_format(((int) ($product['price_pence'] ?? 0)) / 100, 2, '.', '')) ?>">
  </p>
  <p>
    <label for="stock">Stock</label>
    <input type="text" id="stock" name="stock" size="8" value="<?= bt_e((string) (int) ($product['stock'] ?? 0)) ?>">
  </p>
  <p>
    <label class="checkbox"><input type="checkbox" name="discontinued" value="1"<?= (int) ($product['discontinued'] ?? 0) === 1 ? ' checked' : '' ?>> Withdrawn from the catalogue</label>
  </p>
  <p><button type="submit">Save</button></p>
</form>

<p class="small">
  Withdrawing a line leaves it on old orders and takes it off the catalogue and the
  search. It does not clear the stock figure.
</p>
