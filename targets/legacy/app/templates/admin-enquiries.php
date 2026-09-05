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
  Everything sent in through the contact, quote and call-back forms, newest first, up to
  the last five hundred. The sales desk works down the list every morning and marks off
  what has been answered in their own book.
</p>

<p class="small"><a href="/admin/enquiries.php?export=csv">Export for the sales desk</a></p>

<?php if ($rows === []) { ?>
  <p>Nothing has come in yet.</p>
<?php } else { ?>
  <p class="small"><?= count($rows) ?> enquiry(ies) listed.</p>
  <table class="grid">
    <thead>
      <tr>
        <th>Received</th>
        <th>Kind</th>
        <th>Name</th>
        <th>Company</th>
        <th>E-mail</th>
        <th>Telephone</th>
        <th>Message</th>
      </tr>
    </thead>
    <tbody>
    <?php foreach ($rows as $row) { ?>
      <tr>
        <td><?= bt_e(bt_date((string) ($row['created_at'] ?? ''))) ?></td>
        <td><?= bt_e((string) ($row['kind'] ?? '')) ?></td>
        <td><?= bt_e((string) ($row['name'] ?? '')) ?></td>
        <td><?= bt_e((string) ($row['company'] ?? '')) ?></td>
        <td><?= bt_e((string) ($row['email'] ?? '')) ?></td>
        <td><?= bt_e((string) ($row['phone'] ?? '')) ?></td>
        <td><?= bt_e((string) ($row['message'] ?? '')) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
<?php } ?>
