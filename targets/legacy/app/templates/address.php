<table class="plain">
  <tbody>
    <tr><th>Label</th><td><?= bt_e((string) ($address['label'] ?? '')) ?></td></tr>
    <tr><th>Address line 1</th><td><?= bt_e((string) ($address['line1'] ?? '')) ?></td></tr>
    <tr><th>Address line 2</th><td><?= bt_e((string) ($address['line2'] ?? '')) ?></td></tr>
    <tr><th>Town</th><td><?= bt_e((string) ($address['town'] ?? '')) ?></td></tr>
    <tr><th>Postcode</th><td><?= bt_e((string) ($address['postcode'] ?? '')) ?></td></tr>
  </tbody>
</table>

<p class="small">
  This is how the address is printed on the picking note and the delivery label. To have
  it altered or taken off the account, ring the trade desk on 01422 000000.
</p>

<ul class="plain">
  <li><a href="/account/addresses.php">Back to delivery addresses</a></li>
  <li><a href="/account/deliveries.php">Deliveries due</a></li>
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
