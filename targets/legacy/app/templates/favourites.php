<p>
  The lines you keep coming back to. Saving a reference here does not reserve stock; use
  the depot stock page before you set off if it is urgent.
</p>

<?php if ($products === []) { ?>
  <p class="small">Nothing saved yet. Add a reference below, or use the save link on a catalogue page.</p>
<?php } else { ?>
  <table class="grid">
    <thead><tr><th>Reference</th><th>Description</th><th class="num">Price</th><th>Unit</th></tr></thead>
    <tbody>
    <?php foreach ($products as $product) { ?>
      <tr>
        <td><a href="/product.php?ref=<?= bt_e(rawurlencode((string) ($product['reference'] ?? ''))) ?>"><?= bt_e((string) ($product['reference'] ?? '')) ?></a></td>
        <td><?= bt_e((string) ($product['name'] ?? '')) ?></td>
        <td class="num"><?= bt_e(bt_money((int) ($product['price_pence'] ?? 0))) ?></td>
        <td><?= bt_e((string) ($product['unit'] ?? '')) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
  <p class="small">Prices are your account prices and exclude VAT.</p>
<?php } ?>

<h2>Save a line</h2>
<form method="post" action="/account/favourites.php" class="inline">
  <label for="ref">Reference</label>
  <input type="text" id="ref" name="ref" size="16" placeholder="BT-0000">
  <button type="submit">Save it</button>
</form>

<p class="small">
  The reference is the one from the printed catalogue or the top of a delivery note. If it
  is not recognised the line is left out without a fuss.
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
