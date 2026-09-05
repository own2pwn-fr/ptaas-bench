<div class="panel">
  <p>
    Signed in as <?= bt_e((string) ($contact['name'] ?? '')) ?><?php if (trim((string) ($contact['job_title'] ?? '')) !== '') { ?>, <?= bt_e((string) ($contact['job_title'] ?? '')) ?><?php } ?>.
  </p>
  <table class="plain">
    <tbody>
      <tr><th>Company</th><td><?= bt_e((string) ($contact['company'] ?? '')) ?></td></tr>
      <tr><th>Account code</th><td><?= bt_e((string) ($contact['account_code'] ?? '')) ?></td></tr>
      <tr><th>Balance outstanding</th><td class="num"><?= bt_e(bt_money((int) $balance)) ?></td></tr>
    </tbody>
  </table>
  <p class="small">
    The balance is as at the last posting run and takes no account of anything paid in
    today. Terms are 30 days from the end of the month of invoice.
  </p>
</div>

<div class="two-column">
  <div class="main">
    <h2>Your last five orders</h2>
    <?php if ($orders === []) { ?>
      <p class="small">Nothing ordered on this account yet.</p>
    <?php } else { ?>
      <table class="grid">
        <thead><tr><th>Reference</th><th>Placed</th><th class="num">Total</th><th>Status</th></tr></thead>
        <tbody>
        <?php foreach ($orders as $order) { ?>
          <tr>
            <td><a href="/account/order.php?ref=<?= bt_e(rawurlencode((string) ($order['reference'] ?? ''))) ?>"><?= bt_e((string) ($order['reference'] ?? '')) ?></a></td>
            <td><?= bt_e(bt_date((string) ($order['placed_at'] ?? ''))) ?></td>
            <td class="num"><?= bt_e(bt_money((int) ($order['total_pence'] ?? 0))) ?></td>
            <td><?= bt_e((string) ($order['status'] ?? '')) ?></td>
          </tr>
        <?php } ?>
        </tbody>
      </table>
      <p class="small"><a href="/account/orders.php">All orders and quotations</a></p>
    <?php } ?>
  </div>
  <div class="side">
    <h2>Recent paperwork</h2>
    <?php if ($documents === []) { ?>
      <p class="small">No delivery notes or certificates filed yet.</p>
    <?php } else { ?>
      <ul class="plain">
        <?php foreach ($documents as $document) { ?>
          <li>
            <a href="/account/documents.php"><?= bt_e((string) ($document['filename'] ?? '')) ?></a>
            <span class="small"><?= bt_e(bt_date((string) ($document['uploaded_at'] ?? ''))) ?></span>
          </li>
        <?php } ?>
      </ul>
    <?php } ?>
    <p class="small"><a href="/account/documents.php">Send in a signed note</a></p>
  </div>
</div>

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
