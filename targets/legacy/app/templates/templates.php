<p>
  Order templates are the repeat orders your depot builds for you: the van stock, the
  monthly consumables run, the fixings that go out with every job. The counter staff put
  them together from what you have been ordering, and you order the whole lot again in one
  go rather than keying it line by line.
</p>

<?php if ($templates === []) { ?>
  <p class="small">
    Nothing has been built for this account yet. Ask at the counter or ring the trade desk
    on 01422 000000 and they will make one up from your order history.
  </p>
<?php } else { ?>
  <table class="grid">
    <thead><tr><th>Name</th><th class="num">Lines</th><th>Last altered</th></tr></thead>
    <tbody>
    <?php foreach ($templates as $item) { ?>
      <tr>
        <td><?= bt_e((string) ($item['name'] ?? '')) ?></td>
        <td class="num"><?= bt_e((string) ($item['line_count'] ?? '0')) ?></td>
        <td><?= bt_e(bt_date((string) ($item['updated_at'] ?? ''))) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
  <p class="small">
    Quantities are the ones agreed with the depot. To change a template, or to have a new
    one made up, ring the trade desk with the account code and the template name.
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
