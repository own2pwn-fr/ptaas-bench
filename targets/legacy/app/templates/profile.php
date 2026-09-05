<p>
  What we hold against you as a named contact on the account. Orders, statements and
  delivery notes are all filed against the company rather than against you personally.
</p>

<table class="plain">
  <tbody>
    <tr><th>Name</th><td><?= bt_e((string) ($contact['name'] ?? '')) ?></td></tr>
    <tr><th>Job title</th><td><?= bt_e((string) ($contact['job_title'] ?? '')) ?></td></tr>
    <tr><th>Company</th><td><?= bt_e((string) ($contact['company'] ?? '')) ?></td></tr>
    <tr><th>Account code</th><td><?= bt_e((string) ($contact['account_code'] ?? '')) ?></td></tr>
    <tr><th>E-mail address</th><td><?= bt_e((string) ($contact['email'] ?? '')) ?></td></tr>
    <tr><th>Telephone</th><td><?= bt_e((string) ($contact['phone'] ?? '')) ?></td></tr>
  </tbody>
</table>

<p class="notice">
  These details are read-only here. Changes go through the trade desk on 01422 000000, so
  that the credit control office and the depot paperwork stay in step. Ask for the account
  code to be quoted on any change.
</p>

<ul class="plain">
  <li><a href="/account/password.php">Change your password</a></li>
  <li><a href="/account/preferences.php">How the catalogue is laid out for you</a></li>
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
