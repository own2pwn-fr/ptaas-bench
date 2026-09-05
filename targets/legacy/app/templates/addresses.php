<p>
  The addresses our drivers deliver to. Give each one a label the depot will recognise —
  a site name reads better on a picking note than a street name.
</p>

<?php if ($addresses === []) { ?>
  <p class="small">No delivery addresses yet. Everything goes to the address on the account until you add one.</p>
<?php } else { ?>
  <table class="grid">
    <thead><tr><th>Label</th><th>Address</th><th>Town</th><th>Postcode</th></tr></thead>
    <tbody>
    <?php foreach ($addresses as $address) { ?>
      <tr>
        <td><a href="/account/address.php?id=<?= bt_e(rawurlencode((string) ($address['id'] ?? ''))) ?>"><?= bt_e((string) ($address['label'] ?? '')) ?></a></td>
        <td>
          <?= bt_e((string) ($address['line1'] ?? '')) ?><?php if (trim((string) ($address['line2'] ?? '')) !== '') { ?>, <?= bt_e((string) ($address['line2'] ?? '')) ?><?php } ?>
        </td>
        <td><?= bt_e((string) ($address['town'] ?? '')) ?></td>
        <td><?= bt_e((string) ($address['postcode'] ?? '')) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
<?php } ?>

<h2>Add an address</h2>
<form method="post" action="/account/addresses.php" class="stacked">
  <p>
    <label for="label">Label</label>
    <input type="text" id="label" name="label" size="30">
  </p>
  <p>
    <label for="line1">Address line 1</label>
    <input type="text" id="line1" name="line1" size="40">
  </p>
  <p>
    <label for="line2">Address line 2</label>
    <input type="text" id="line2" name="line2" size="40">
  </p>
  <p>
    <label for="town">Town</label>
    <input type="text" id="town" name="town" size="30">
  </p>
  <p>
    <label for="postcode">Postcode</label>
    <input type="text" id="postcode" name="postcode" size="10">
  </p>
  <p><button type="submit">Add it</button></p>
</form>

<p class="small">
  Site deliveries need someone there to sign. If the gate is shut when the driver arrives
  the goods come back to the depot and go out again on the next round.
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
