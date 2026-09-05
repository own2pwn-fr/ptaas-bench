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
  <p class="notice">Saved. The site reads these on the next request.</p>
<?php } ?>

<p>
  The handful of figures the front of the site quotes back at customers. They are held as
  plain text, so type them the way they should read on the page.
</p>

<?php
  $btFields = [
      'carriage_free_over' => ['Carriage free over', 'The net order value above which carriage is not charged, in pounds. Shown on the delivery page.'],
      'cutoff_time' => ['Cut-off time', 'The time by which a stock order must be placed for next working day delivery.'],
      'counter_open' => ['Counter hours', 'The hours printed against every depot on the depot pages.'],
      'notice' => ['Notice', 'A short sentence carried across the top of the site. Leave it empty for nothing.'],
  ];
?>

<form method="post" action="/admin/settings.php" class="stacked">
  <?php foreach ($btFields as $btKey => $btField) { ?>
    <p>
      <label for="<?= bt_e($btKey) ?>"><?= bt_e($btField[0]) ?></label>
      <input type="text" id="<?= bt_e($btKey) ?>" name="<?= bt_e($btKey) ?>" size="50" value="<?= bt_e((string) ($settings[$btKey] ?? '')) ?>">
      <span class="small"><?= bt_e($btField[1]) ?></span>
    </p>
  <?php } ?>
  <p><button type="submit">Save</button></p>
</form>

<p class="small">
  Two hundred characters a field. Anything longer is cut when it is written, which is why
  the notice has to be a sentence and not a paragraph.
</p>
