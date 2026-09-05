<?php
$bt_ratings = [
    5 => '5 — could not have been better',
    4 => '4 — good',
    3 => '3 — about what I expected',
    2 => '2 — not good enough',
    1 => '1 — poor',
];
?>
<?php if ($sent) { ?>
  <p class="notice">Thank you. Your comments go to the depot manager and to the directors' meeting each month.</p>
<?php } ?>

<p>
  Tell us how we did. We read all of it, and where something has gone wrong at a counter
  the depot manager sees it by name the same week. If you would like an answer, put your
  telephone number in the box with your comments.
</p>

<form method="post" action="/feedback.php" class="stacked">
  <p>
    <label for="rating">How did we do?</label>
    <select id="rating" name="rating">
      <?php foreach ($bt_ratings as $value => $label) { ?>
        <option value="<?= bt_e((string) (int) $value) ?>"<?= (int) $value === 3 ? ' selected="selected"' : '' ?>><?= bt_e((string) $label) ?></option>
      <?php } ?>
    </select>
  </p>
  <p><label for="depot">Which depot?</label><input type="text" id="depot" name="depot" size="30"></p>
  <p><label for="comment">Your comments</label><textarea id="comment" name="comment" rows="7" cols="50"></textarea></p>
  <p><button type="submit">Send it in</button></p>
</form>

<p class="small">
  A complaint about an invoice or a delivery is better raised with the trade desk on
  01422 000000, where it can be put right the same day.
</p>
