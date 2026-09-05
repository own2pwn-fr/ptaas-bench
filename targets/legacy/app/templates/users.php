<p>
  Who can sign in to this account here, and when the site last saw them. Ordering at the
  counter is not shown — this is the website only.
</p>

<?php if ($people === []) { ?>
  <p class="small">Nobody has a sign-in on this account yet.</p>
<?php } else { ?>
  <table class="grid">
    <thead><tr><th>Name</th><th>E-mail address</th><th>Last signed in</th></tr></thead>
    <tbody>
    <?php foreach ($people as $person) { ?>
      <tr>
        <td><?= bt_e((string) ($person['name'] ?? '')) ?></td>
        <td><?= bt_e((string) ($person['email'] ?? '')) ?></td>
        <td>
          <?php if (trim((string) ($person['last_seen_at'] ?? '')) === '') { ?>
            <span class="small">never</span>
          <?php } else { ?>
            <?= bt_e(bt_date((string) ($person['last_seen_at'] ?? ''))) ?>
          <?php } ?>
        </td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
<?php } ?>

<p class="small">
  Sign-ins are personal: a password is not to be shared round the office. If somebody has
  left, ring the trade desk and their access will be withdrawn the same day.
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
