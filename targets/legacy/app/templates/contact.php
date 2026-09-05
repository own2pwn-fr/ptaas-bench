<?php
// The form comes back with the customer's own words in it when something is missing, so
// a long enquiry does not have to be typed twice.
$values = $values ?? [];
?>
<?php if ($sent) { ?>
  <p class="notice">Your enquiry has gone to the trade desk. Somebody will come back to you within one working day.</p>
<?php } ?>

<?php if ($errors !== []) { ?>
  <div class="error">
    <p>We could not send that on:</p>
    <ul class="plain">
    <?php foreach ($errors as $error) { ?>
      <li><?= bt_e((string) $error) ?></li>
    <?php } ?>
    </ul>
  </div>
<?php } ?>

<p>
  Head office is at Lowfields Way, Elland, West Yorkshire, and the trade desk answers on
  01422 000000 from seven in the morning until five. If your enquiry is about a line or a
  price, the quickest route is the depot you normally deal with; the numbers are below.
</p>

<form method="post" action="/contact.php" class="stacked">
  <p><label for="name">Your name</label><input type="text" id="name" name="name" size="34" value="<?= bt_e((string) ($values['name'] ?? '')) ?>"></p>
  <p><label for="company">Company</label><input type="text" id="company" name="company" size="34" value="<?= bt_e((string) ($values['company'] ?? '')) ?>"></p>
  <p><label for="email">E-mail</label><input type="text" id="email" name="email" size="34" value="<?= bt_e((string) ($values['email'] ?? '')) ?>"></p>
  <p><label for="phone">Telephone</label><input type="text" id="phone" name="phone" size="20" value="<?= bt_e((string) ($values['phone'] ?? '')) ?>"></p>
  <p><label for="message">Your enquiry</label><textarea id="message" name="message" rows="7" cols="50"><?= bt_e((string) ($values['message'] ?? '')) ?></textarea></p>
  <p><button type="submit">Send it to the trade desk</button></p>
</form>

<h2>The depots</h2>

<?php if ($branches === []) { ?>
  <p>The depot numbers are unavailable at the moment. Head office is on 01422 000000.</p>
<?php } else { ?>
  <table class="grid">
    <thead><tr><th>Depot</th><th>Town</th><th>Postcode</th><th>Telephone</th></tr></thead>
    <tbody>
    <?php foreach ($branches as $branch) { ?>
      <tr>
        <td><?= bt_e((string) ($branch['name'] ?? '')) ?></td>
        <td><?= bt_e((string) ($branch['town'] ?? '')) ?></td>
        <td><?= bt_e((string) ($branch['postcode'] ?? '')) ?></td>
        <td><?= bt_e((string) ($branch['phone'] ?? '')) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
<?php } ?>

<p class="small">
  Accounts queries go to the sales ledger at Elland on 01422 000000, extension 240.
  Would you rather we rang you? <a href="/callback.php">Ask for a call back</a>.
</p>
