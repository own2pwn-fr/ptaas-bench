<?php if (trim((string) $message) !== '') { ?>
  <p class="notice"><?= bt_e((string) $message) ?></p>
<?php } ?>

<p>
  Eight characters at least. Something you can type at a counter terminal without peering
  at a note in your wallet is worth more than something clever you have to write down.
</p>

<form method="post" action="/account/password.php" class="stacked">
  <p>
    <label for="current">Current password</label>
    <input type="password" id="current" name="current" size="34">
  </p>
  <p>
    <label for="new">New password</label>
    <input type="password" id="new" name="new" size="34">
  </p>
  <p><button type="submit">Change it</button></p>
</form>

<ul class="plain">
  <li><a href="/account/reset.php">Forgotten the current one?</a></li>
  <li><a href="/account/users.php">Who else can sign in</a></li>
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
