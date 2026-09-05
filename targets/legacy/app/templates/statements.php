<p>
  Monthly statements and the invoices behind them, oldest at the bottom. Statements are
  issued on the first working day of the month and are due for settlement thirty days
  from the end of the month of invoice.
</p>

<?php if ($statements === []) { ?>
  <p class="small">No statements have been issued on this account yet.</p>
<?php } else { ?>
  <table class="grid">
    <thead><tr><th>Period</th><th>File</th><th>Issued</th><th class="num">Total</th></tr></thead>
    <tbody>
    <?php foreach ($statements as $statement) { ?>
      <tr>
        <td><?= bt_e((string) ($statement['period'] ?? '')) ?></td>
        <td><a href="/account/invoice.php?file=<?= bt_e(rawurlencode((string) ($statement['filename'] ?? ''))) ?>"><?= bt_e((string) ($statement['filename'] ?? '')) ?></a></td>
        <td><?= bt_e(bt_date((string) ($statement['issued_at'] ?? ''))) ?></td>
        <td class="num"><?= bt_e(bt_money((int) ($statement['total_pence'] ?? 0))) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
  <p class="small">
    If a statement does not agree with your ledger, the accounts office at Elland is on
    01422 000000 and will want the account code and the period.
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
