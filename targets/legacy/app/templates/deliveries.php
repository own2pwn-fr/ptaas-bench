<p>
  Orders picked or already on a van. Depot rounds leave Elland at 7am and the outlying
  depots between 7am and half past, so anything picked overnight is normally with you the
  same morning.
</p>

<?php if ($deliveries === []) { ?>
  <p class="small">Nothing is due at the moment. Everything ordered has either been delivered or is still being picked.</p>
<?php } else { ?>
  <table class="grid">
    <thead><tr><th>Reference</th><th>Placed</th><th>Going to</th><th>Town</th><th>Status</th></tr></thead>
    <tbody>
    <?php foreach ($deliveries as $delivery) { ?>
      <tr>
        <td><a href="/account/order.php?ref=<?= bt_e(rawurlencode((string) ($delivery['reference'] ?? ''))) ?>"><?= bt_e((string) ($delivery['reference'] ?? '')) ?></a></td>
        <td><?= bt_e(bt_date((string) ($delivery['placed_at'] ?? ''))) ?></td>
        <td>
          <?php if (trim((string) ($delivery['label'] ?? '')) === '') { ?>
            <span class="small">the account address</span>
          <?php } else { ?>
            <?= bt_e((string) ($delivery['label'] ?? '')) ?>
          <?php } ?>
        </td>
        <td><?= bt_e((string) ($delivery['town'] ?? '')) ?></td>
        <td><?= bt_e((string) ($delivery['status'] ?? '')) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
  <p class="small">
    Somebody needs to be there to sign. Once the driver is back, send the signed note in
    on the <a href="/account/documents.php">delivery paperwork</a> page.
  </p>
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
