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
  Orders newest first, two hundred to a page. Leave the box on Any to see the lot, or
  pick a stage to work through the picking list for the day.
</p>

<form method="get" action="/admin/orders.php" class="inline">
  <label for="status">Stage</label>
  <select id="status" name="status">
    <?php
      $btStages = [
          '' => 'Any',
          'placed' => 'Placed',
          'picked' => 'Picked',
          'out for delivery' => 'Out for delivery',
          'delivered' => 'Delivered',
          'cancelled' => 'Cancelled',
      ];
    ?>
    <?php foreach ($btStages as $btValue => $btLabel) { ?>
      <option value="<?= bt_e((string) $btValue) ?>"<?= (string) $status === (string) $btValue ? ' selected' : '' ?>><?= bt_e($btLabel) ?></option>
    <?php } ?>
  </select>
  <button type="submit">Show</button>
</form>

<?php if ($orders === []) { ?>
  <p>No orders at that stage.</p>
<?php } else { ?>
  <p class="small"><?= count($orders) ?> order(s) listed.</p>
  <table class="grid">
    <thead>
      <tr><th>Reference</th><th>Placed</th><th>Company</th><th class="num">Value</th><th>Where it is</th></tr>
    </thead>
    <tbody>
    <?php foreach ($orders as $order) { ?>
      <tr>
        <td><a href="/admin/order.php?ref=<?= bt_e(rawurlencode((string) ($order['reference'] ?? ''))) ?>"><?= bt_e((string) ($order['reference'] ?? '')) ?></a></td>
        <td><?= bt_e(bt_date((string) ($order['placed_at'] ?? ''))) ?></td>
        <td><?= bt_e((string) ($order['company'] ?? '')) ?></td>
        <td class="num"><?= bt_e(bt_money((int) ($order['total_pence'] ?? 0))) ?></td>
        <td><?= bt_e((string) ($order['status'] ?? '')) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
<?php } ?>
