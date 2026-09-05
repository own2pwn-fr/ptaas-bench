<p>
  Everyone we have on file for this account. These are the people the depots ring about a
  short pick or a delivery slot, so it is worth keeping the numbers current.
</p>

<?php if ($people === []) { ?>
  <p class="small">Nobody else is listed on the account.</p>
<?php } else { ?>
  <table class="grid">
    <thead><tr><th>Name</th><th>Job title</th><th>E-mail address</th><th>Telephone</th></tr></thead>
    <tbody>
    <?php foreach ($people as $person) { ?>
      <tr>
        <td><?= bt_e((string) ($person['name'] ?? '')) ?></td>
        <td><?= bt_e((string) ($person['job_title'] ?? '')) ?></td>
        <td><?= bt_e((string) ($person['email'] ?? '')) ?></td>
        <td><?= bt_e((string) ($person['phone'] ?? '')) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
<?php } ?>

<p class="notice">
  New people are added by the trade desk on 01422 000000. Tell them the account code, the
  name and whether the person is to be allowed to place orders on credit; somebody who has
  left is taken off the same way, the same day.
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
