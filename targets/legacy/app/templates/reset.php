<p>
  Give us the e-mail address the account is held under and we will send a link that lets
  you set a new password. It is normally the address the statements go to rather than a
  personal one, so it is worth checking the top of last month's statement first.
</p>

<?php if (trim((string) $message) !== '') { ?>
  <p class="notice"><?= bt_e((string) $message) ?></p>
<?php } ?>

<?php if ($known === false) { ?>
  <p class="small">
    If you are not sure which address the account uses, the trade desk is on 01422 000000
    from 7am and can tell you which one is on file.
  </p>
<?php } ?>

<form method="post" action="/account/reset.php" class="stacked">
  <p>
    <label for="email">E-mail address</label>
    <input type="text" id="email" name="email" size="34">
  </p>
  <p><button type="submit">Send me the link</button></p>
</form>

<ul class="plain">
  <li><a href="/account/login.php">Back to signing in</a></li>
  <li><a href="/account/register.php">Apply for a trade account</a></li>
  <li><a href="/help.php?topic=account-application.php">What an account gets you</a></li>
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
