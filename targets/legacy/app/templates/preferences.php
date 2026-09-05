<?php if ($saved) { ?>
  <p class="notice">Your preferences have been kept.</p>
<?php } ?>

<p>
  How the catalogue is laid out for
  <?= bt_e((string) ($contact['name'] ?? 'you')) ?>. The grid suits browsing for tools; the
  list suits working down a fixings schedule with the references in front of you.
</p>

<form method="post" action="/account/preferences.php" class="stacked">
  <p>
    <label for="listing">Catalogue layout</label>
    <select id="listing" name="listing">
      <option value="grid">Grid of pictures</option>
      <option value="list">Plain list</option>
    </select>
  </p>
  <p>
    <label for="per_page">Lines to a page</label>
    <select id="per_page" name="per_page">
      <option value="12">12</option>
      <option value="24">24</option>
      <option value="48">48</option>
      <option value="96">96</option>
    </select>
  </p>
  <p><button type="submit">Keep these settings</button></p>
</form>

<p class="small">
  The choice is kept in a cookie on this machine rather than against the account, so it
  follows you between depots when you sign in on a counter terminal. Clear your cookies
  and it goes back to the grid at 24 lines a page.
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
