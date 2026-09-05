<?php if (trim((string) $saved) !== '') { ?>
  <p class="notice">
    Your request against <?= bt_e((string) $saved) ?> is with the trade desk. They will
    price it and come back to you, usually the same day.
  </p>
<?php } ?>

<p>
  Tell us what you need and the trade desk at Elland will price it against your account
  terms. Give quantities and, where it matters, the standard the goods have to meet.
</p>

<form method="post" action="/account/quote.php" class="stacked">
  <p>
    <label for="reference">Your purchase order reference</label>
    <input type="text" id="reference" name="reference" size="24">
  </p>
  <p>
    <label for="note">What do you need?</label>
    <textarea id="note" name="note" rows="8" cols="50"></textarea>
  </p>
  <p><button type="submit">Send it to the trade desk</button></p>
</form>

<h2>Quotations already raised</h2>
<?php if ($quotes === []) { ?>
  <p class="small">Nothing raised on this account yet.</p>
<?php } else { ?>
  <table class="grid">
    <thead><tr><th>Number</th><th>Your reference</th><th>Raised</th><th>Status</th></tr></thead>
    <tbody>
    <?php foreach ($quotes as $quote) { ?>
      <tr>
        <td>Q-<?= bt_e(str_pad((string) ($quote['id'] ?? ''), 6, '0', STR_PAD_LEFT)) ?></td>
        <td><?= bt_e((string) ($quote['reference'] ?? '')) ?></td>
        <td><?= bt_e(bt_date((string) ($quote['created_at'] ?? ''))) ?></td>
        <td><?= bt_e((string) ($quote['status'] ?? '')) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
  <p class="small">Quotations hold for thirty days from the date they were raised.</p>
<?php } ?>

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
