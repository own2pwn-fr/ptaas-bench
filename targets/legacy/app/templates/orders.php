<p>
  Everything placed on the account, whether it went through the counter, the trade desk or
  this site. Quotations are listed underneath and stay open for thirty days.
</p>

<form method="get" action="/account/orders.php" class="inline">
  <label for="ref">Filter by our reference</label>
  <input type="text" id="ref" name="ref" size="16" value="<?= bt_e((string) $ref) ?>">
  <button type="submit">Filter</button>
</form>

<?php if (trim((string) $ref) !== '') { ?>
  <p class="small"><?= count($orders) ?> order(s) matched that reference. <a href="/account/orders.php">Show them all</a>.</p>
<?php } ?>

<h2>Orders</h2>
<?php if ($orders === []) { ?>
  <p class="small">No orders to show.</p>
<?php } else { ?>
  <table class="grid">
    <thead><tr><th>Our reference</th><th>Your reference</th><th>Placed</th><th class="num">Total</th><th>Status</th></tr></thead>
    <tbody>
    <?php foreach ($orders as $order) { ?>
      <tr>
        <td><a href="/account/order.php?ref=<?= bt_e(rawurlencode((string) ($order['reference'] ?? ''))) ?>"><?= bt_e((string) ($order['reference'] ?? '')) ?></a></td>
        <td><?= bt_e((string) ($order['po_reference'] ?? '')) ?></td>
        <td><?= bt_e(bt_date((string) ($order['placed_at'] ?? ''))) ?></td>
        <td class="num"><?= bt_e(bt_money((int) ($order['total_pence'] ?? 0))) ?></td>
        <td><?= bt_e((string) ($order['status'] ?? '')) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
<?php } ?>

<h2>Quotations</h2>
<?php if ($quotes === []) { ?>
  <p class="small">Nothing quoted at the moment. <a href="/account/quote.php">Ask the trade desk for a price</a>.</p>
<?php } else { ?>
  <table class="grid">
    <thead><tr><th>Reference</th><th>Raised</th><th class="num">Total</th><th>Status</th></tr></thead>
    <tbody>
    <?php foreach ($quotes as $quote) { ?>
      <tr>
        <td><?= bt_e((string) ($quote['reference'] ?? '')) ?></td>
        <td><?= bt_e(bt_date((string) ($quote['created_at'] ?? ''))) ?></td>
        <td class="num"><?= bt_e(bt_money((int) ($quote['total_pence'] ?? 0))) ?></td>
        <td><?= bt_e((string) ($quote['status'] ?? '')) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
<?php } ?>

<p class="small">
  <a href="/account/orders.php?export=csv">Download as a spreadsheet</a> —
  both tables in one file, for reconciling against your purchase ledger.
</p>

<h2>Elsewhere in your account</h2>
<ul class="inline small">
  <li><a href="/account/index.php">Summary</a></li>
  <li><a href="/account/orders.php">Orders and quotations</a></li>
  <li><a href="/account/statements.php">Statements</a></li>
  <li><a href="/account/documents.php">Delivery paperwork</a></li>
  <li><a href="/account/deliveries.php">Deliveries due</a></li>
  <li><a href="/account/favourites.php">Saved lines</a></li>
  <li><a href="/account/templates.php">Order templates</a></li>
  <li><a href="/account/addresses.php">Delivery addresses</a></li>
  <li><a href="/account/contacts.php">People on the account</a></li>
  <li><a href="/account/users.php">Sign-in access</a></li>
  <li><a href="/account/quote.php">Request a quotation</a></li>
  <li><a href="/account/profile.php">Your details</a></li>
  <li><a href="/account/preferences.php">Preferences</a></li>
  <li><a href="/account/password.php">Change your password</a></li>
  <li><a href="/account/logout.php">Sign out</a></li>
</ul>
