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

<p class="small"><a href="/admin/orders.php">Back to the order list</a></p>

<table class="grid">
  <tbody>
    <tr><th>Reference</th><td><?= bt_e((string) ($order['reference'] ?? '')) ?></td></tr>
    <tr><th>Placed</th><td><?= bt_e(bt_date((string) ($order['placed_at'] ?? ''))) ?></td></tr>
    <tr><th>Company</th><td><a href="/admin/customer.php?code=<?= bt_e(rawurlencode((string) ($order['account_code'] ?? ''))) ?>"><?= bt_e((string) ($order['company'] ?? '')) ?></a></td></tr>
    <tr><th>Account</th><td><?= bt_e((string) ($order['account_code'] ?? '')) ?></td></tr>
    <tr><th>Their order number</th><td><?= bt_e((string) ($order['po_reference'] ?? '')) ?></td></tr>
    <tr><th>Where it is</th><td><?= bt_e((string) ($order['status'] ?? '')) ?></td></tr>
    <tr><th>Value</th><td class="num"><?= bt_e(bt_money((int) ($order['total_pence'] ?? 0))) ?></td></tr>
  </tbody>
</table>

<h2>Lines</h2>

<?php if ($lines === []) { ?>
  <p>No lines against this order. It was most likely raised at a counter and never picked.</p>
<?php } else { ?>
  <table class="grid">
    <thead>
      <tr><th>Reference</th><th>Description</th><th class="num">Quantity</th><th class="num">Each</th><th class="num">Line</th></tr>
    </thead>
    <tbody>
    <?php foreach ($lines as $line) { ?>
      <tr>
        <td><a href="/admin/product.php?ref=<?= bt_e(rawurlencode((string) ($line['reference'] ?? ''))) ?>"><?= bt_e((string) ($line['reference'] ?? '')) ?></a></td>
        <td><?= bt_e((string) ($line['name'] ?? '')) ?></td>
        <td class="num"><?= bt_e((string) (int) ($line['quantity'] ?? 0)) ?></td>
        <td class="num"><?= bt_e(bt_money((int) ($line['price_pence'] ?? 0))) ?></td>
        <td class="num"><?= bt_e(bt_money((int) ($line['price_pence'] ?? 0) * (int) ($line['quantity'] ?? 0))) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
  <p class="small">
    Line prices are the ones held at the moment the order was taken, not the price on the
    catalogue today. Carriage and value added tax are not shown here.
  </p>
<?php } ?>
