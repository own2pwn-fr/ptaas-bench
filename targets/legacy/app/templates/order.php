<table class="plain">
  <tbody>
    <tr><th>Our reference</th><td><?= bt_e((string) ($order['reference'] ?? '')) ?></td></tr>
    <tr><th>Your reference</th><td><?= bt_e((string) ($order['po_reference'] ?? '')) ?></td></tr>
    <tr><th>Placed</th><td><?= bt_e(bt_date((string) ($order['placed_at'] ?? ''))) ?></td></tr>
    <tr><th>Status</th><td><?= bt_e((string) ($order['status'] ?? '')) ?></td></tr>
    <tr><th>Order total</th><td class="num"><?= bt_e(bt_money((int) ($order['total_pence'] ?? 0))) ?></td></tr>
  </tbody>
</table>

<h2>Lines</h2>
<?php if ($lines === []) { ?>
  <p class="small">No lines are recorded against this order. Ring the trade desk if that looks wrong.</p>
<?php } else { ?>
  <table class="grid">
    <thead>
      <tr>
        <th>Reference</th><th>Description</th><th>Unit</th>
        <th class="num">Quantity</th><th class="num">Price</th><th class="num">Line total</th>
      </tr>
    </thead>
    <tbody>
    <?php foreach ($lines as $line) { ?>
      <tr>
        <td><a href="/product.php?ref=<?= bt_e(rawurlencode((string) ($line['reference'] ?? ''))) ?>"><?= bt_e((string) ($line['reference'] ?? '')) ?></a></td>
        <td><?= bt_e((string) ($line['name'] ?? '')) ?></td>
        <td><?= bt_e((string) ($line['unit'] ?? '')) ?></td>
        <td class="num"><?= bt_e((string) ($line['quantity'] ?? '')) ?></td>
        <td class="num"><?= bt_e(bt_money((int) ($line['price_pence'] ?? 0))) ?></td>
        <td class="num"><?= bt_e(bt_money((int) ($line['quantity'] ?? 0) * (int) ($line['price_pence'] ?? 0))) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
  <p class="small">
    Prices are per unit and exclude VAT. Line totals are worked out from the quantity
    picked, so a short-picked line will read lower than the original order.
  </p>
<?php } ?>

<ul class="plain">
  <li><a href="/account/orders.php">Back to all orders</a></li>
  <li><a href="/account/deliveries.php">Deliveries due</a></li>
  <li><a href="/account/documents.php">Send in the signed delivery note</a></li>
</ul>

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
