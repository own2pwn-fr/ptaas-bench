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
  The last two hundred entries, newest first. Price changes, stock corrections, settings
  and sign-ins all land here. The trail is kept for six years for the auditors and is not
  edited from this page.
</p>

<?php if ($rows === []) { ?>
  <p>Nothing recorded.</p>
<?php } else { ?>
  <table class="grid">
    <thead><tr><th>When</th><th>Who</th><th>What</th><th>Detail</th></tr></thead>
    <tbody>
    <?php foreach ($rows as $row) { ?>
      <tr>
        <td><?= bt_e(bt_date((string) ($row['created_at'] ?? ''))) ?></td>
        <td><?= bt_e((string) ($row['actor'] ?? '')) ?></td>
        <td><?= bt_e((string) ($row['action'] ?? '')) ?></td>
        <td><?= bt_e((string) ($row['detail'] ?? '')) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
<?php } ?>
