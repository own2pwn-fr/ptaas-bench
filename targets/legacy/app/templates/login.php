<?php if (trim((string) $notice) !== '') { ?>
  <!-- the banner is meant to carry a short sentence with a link in it -->
  <p class="notice"><?= bt_out('signin.notice.markup_escape', 'notice', (string) $notice) ?></p>
<?php } ?>

<?php if ($error !== '') { ?>
  <p class="error"><?= bt_e($error) ?></p>
<?php } ?>

<form method="post" action="/account/login.php" class="stacked">
  <p>
    <label for="email">E-mail address</label>
    <input type="text" id="email" name="email" size="34" value="<?= bt_e($email) ?>">
  </p>
  <p>
    <label for="password">Password</label>
    <input type="password" id="password" name="password" size="34">
  </p>
  <p>
    <label class="checkbox"><input type="checkbox" name="remember" value="1"> Keep me signed in on this machine</label>
  </p>
  <p><button type="submit">Sign in</button></p>
</form>

<ul class="plain">
  <li><a href="/account/reset.php">Forgotten your password?</a></li>
  <li><a href="/account/register.php">Apply for a trade account</a></li>
  <li><a href="/help.php?topic=account-application.php">What an account gets you</a></li>
</ul>
