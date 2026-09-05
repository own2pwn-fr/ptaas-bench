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

<p class="small"><a href="/admin/customers.php">Back to the account list</a></p>

<h2>Account</h2>

<table class="grid">
  <tbody>
    <tr><th>Account code</th><td><?= bt_e((string) ($customer['account_code'] ?? '')) ?></td></tr>
    <tr><th>Company</th><td><?= bt_e((string) ($customer['company'] ?? '')) ?></td></tr>
    <tr><th>Town</th><td><?= bt_e((string) ($customer['town'] ?? '')) ?></td></tr>
    <tr><th>Postcode</th><td><?= bt_e((string) ($customer['postcode'] ?? '')) ?></td></tr>
    <tr><th>Terms</th><td><?= bt_e((string) ($customer['terms'] ?? '')) ?></td></tr>
    <tr><th>Credit limit</th><td class="num"><?= bt_e(bt_money((int) ($customer['credit_limit_pence'] ?? 0))) ?></td></tr>
    <tr><th>Balance</th><td class="num"><?= bt_e(bt_money((int) ($customer['balance_pence'] ?? 0))) ?></td></tr>
  </tbody>
</table>

<h2>Contacts</h2>

<?php if ($contacts === []) { ?>
  <p>No contacts recorded. The trade desk should take a name the next time they ring in.</p>
<?php } else { ?>
  <table class="grid">
    <thead><tr><th>Name</th><th>Position</th><th>E-mail</th><th>Telephone</th></tr></thead>
    <tbody>
    <?php foreach ($contacts as $contact) { ?>
      <tr>
        <td><?= bt_e((string) ($contact['name'] ?? '')) ?></td>
        <td><?= bt_e((string) ($contact['job_title'] ?? '')) ?></td>
        <td><?= bt_e((string) ($contact['email'] ?? '')) ?></td>
        <td><?= bt_e((string) ($contact['phone'] ?? '')) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
<?php } ?>

<h2>Recent orders</h2>

<?php if ($orders === []) { ?>
  <p>Nothing ordered on this account yet.</p>
<?php } else { ?>
  <table class="grid">
    <thead><tr><th>Reference</th><th>Placed</th><th class="num">Value</th><th>Where it is</th></tr></thead>
    <tbody>
    <?php foreach ($orders as $order) { ?>
      <tr>
        <td><a href="/admin/order.php?ref=<?= bt_e(rawurlencode((string) ($order['reference'] ?? ''))) ?>"><?= bt_e((string) ($order['reference'] ?? '')) ?></a></td>
        <td><?= bt_e(bt_date((string) ($order['placed_at'] ?? ''))) ?></td>
        <td class="num"><?= bt_e(bt_money((int) ($order['total_pence'] ?? 0))) ?></td>
        <td><?= bt_e((string) ($order['status'] ?? '')) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
  <p class="small">The last twenty only. Credit control take the full history off the ledger.</p>
<?php } ?>
