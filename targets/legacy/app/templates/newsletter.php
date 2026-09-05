<?php if ($sent) { ?>
  <p class="notice">You are on the list. The next one goes out at the start of the month.</p>
<?php } ?>

<?php if (isset($error) && trim((string) $error) !== '') { ?>
  <p class="error"><?= bt_e((string) $error) ?></p>
<?php } ?>

<p>
  One e-mail a month: the offers for the month, new lines the buying office has taken on,
  and anything the depots need to tell you about holiday opening or van runs. It is
  written by the trade desk, not by an agency, and it is short.
</p>

<form method="post" action="/newsletter.php" class="inline">
  <label for="email">E-mail address</label>
  <input type="text" id="email" name="email" size="34">
  <button type="submit">Put me on the list</button>
</form>

<p class="small">
  We do not pass addresses on to anybody. Every issue carries a line at the bottom for
  coming off the list, or you can ring the trade desk and ask. See our
  <a href="/privacy.php">privacy page</a>.
</p>
