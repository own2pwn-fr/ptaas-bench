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
  The morning figures, as they stood when this page was opened. All four are everything on file. Nothing here is cached, so a
  refresh after the overnight run will move the numbers.
</p>

<ul class="tiles">
  <li>
    <span class="num"><?= bt_e((string) (int) ($counts['orders'] ?? 0)) ?></span>
    <span class="small">Orders on file</span>
  </li>
  <li>
    <span class="num"><?= bt_e((string) (int) ($counts['enquiries'] ?? 0)) ?></span>
    <span class="small">Enquiries on file</span>
  </li>
  <li>
    <span class="num"><?= bt_e((string) (int) ($counts['customers'] ?? 0)) ?></span>
    <span class="small">Accounts</span>
  </li>
  <li>
    <span class="num"><?= bt_e((string) (int) ($counts['lines'] ?? 0)) ?></span>
    <span class="small">Catalogue lines still listed</span>
  </li>
</ul>

<h2>Panels</h2>

<?php if ($panels === []) { ?>
  <p>No panels are set up. Operations add them by dropping a partial into the panel folder.</p>
<?php } else { ?>
  <ul class="plain">
    <?php foreach ($panels as $panel) { ?>
      <li>
        <a href="/admin/widget.php?source=<?= bt_e(rawurlencode((string) ($panel[0] ?? ''))) ?>"><?= bt_e((string) ($panel[1] ?? '')) ?></a>
        <span class="small"><code><?= bt_e((string) ($panel[0] ?? '')) ?></code></span>
      </li>
    <?php } ?>
  </ul>
<?php } ?>

<p class="small">
  The depot terminals are meant to sit on this page all day. If a figure looks wrong,
  check the activity list before ringing Elland.
</p>
