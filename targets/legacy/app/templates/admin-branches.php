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
  The eight depots and the central warehouse at Elland, as they appear on the depot pages
  and on the collection list at the checkout. Ring the head office switchboard to have a
  manager or a number changed.
</p>

<?php if ($branches === []) { ?>
  <p>No depots on file, which will empty the depot pages on the front of the site.</p>
<?php } else { ?>
  <table class="grid">
    <thead><tr><th>Depot</th><th>Town</th><th>Postcode</th><th>Telephone</th><th>Manager</th></tr></thead>
    <tbody>
    <?php foreach ($branches as $branch) { ?>
      <tr>
        <td><?= bt_e((string) ($branch['name'] ?? '')) ?></td>
        <td><?= bt_e((string) ($branch['town'] ?? '')) ?></td>
        <td><?= bt_e((string) ($branch['postcode'] ?? '')) ?></td>
        <td><?= bt_e((string) ($branch['phone'] ?? '')) ?></td>
        <td><?= bt_e((string) ($branch['manager'] ?? '')) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
  <p class="small"><?= count($branches) ?> depot(s) listed.</p>
<?php } ?>
