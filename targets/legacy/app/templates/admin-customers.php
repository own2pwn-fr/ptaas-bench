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
  Accounts in company order, two hundred to a page. Balances are as at the last posting
  from the ledger, so anything taken at a counter this morning may not be on here yet.
</p>

<?php if ($customers === []) { ?>
  <p>No accounts on file.</p>
<?php } else { ?>
  <table class="grid">
    <thead>
      <tr>
        <th>Account</th>
        <th>Company</th>
        <th>Town</th>
        <th class="num">Credit limit</th>
        <th class="num">Balance</th>
      </tr>
    </thead>
    <tbody>
    <?php foreach ($customers as $customer) { ?>
      <tr>
        <td><a href="/admin/customer.php?code=<?= bt_e(rawurlencode((string) ($customer['account_code'] ?? ''))) ?>"><?= bt_e((string) ($customer['account_code'] ?? '')) ?></a></td>
        <td><?= bt_e((string) ($customer['company'] ?? '')) ?></td>
        <td><?= bt_e((string) ($customer['town'] ?? '')) ?></td>
        <td class="num"><?= bt_e(bt_money((int) ($customer['credit_limit_pence'] ?? 0))) ?></td>
        <td class="num"><?= bt_e(bt_money((int) ($customer['balance_pence'] ?? 0))) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
  <p class="small">
    An account whose balance is above its limit will not take a new order at a counter
    until credit control have released it.
  </p>
<?php } ?>
