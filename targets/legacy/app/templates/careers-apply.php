<?php if ($sent) { ?>
  <p class="notice">Your application is with the personnel office at Elland. We reply to everyone, though it can take a fortnight in a busy period.</p>
<?php } ?>

<p>
  Fill this in for any of the vacancies on the list. If you would rather send a curriculum
  vitae, post it to the personnel office at Lowfields Way, Elland, West Yorkshire, marked
  for the attention of the personnel manager.
</p>

<?php if ($vacancies === []) { ?>
  <p class="notice">
    There is nothing open at the moment. You are welcome to send your details anyway;
    they are kept on file for six months and the counters take people on through the year.
  </p>
<?php } ?>

<form method="post" action="/careers-apply.php" class="stacked">
  <p><label for="name">Your name</label><input type="text" id="name" name="name" size="34"></p>
  <p><label for="email">E-mail</label><input type="text" id="email" name="email" size="34"></p>
  <p><label for="phone">Telephone</label><input type="text" id="phone" name="phone" size="20"></p>
  <p>
    <label for="vacancy">Which vacancy?</label>
    <select id="vacancy" name="vacancy">
      <option value="">Any that comes up</option>
      <?php foreach ($vacancies as $vacancy) { ?>
        <option value="<?= bt_e((string) ($vacancy['slug'] ?? '')) ?>"><?= bt_e((string) ($vacancy['title'] ?? '')) ?> — <?= bt_e((string) ($vacancy['location'] ?? '')) ?></option>
      <?php } ?>
    </select>
  </p>
  <p><label for="note">Covering note</label><textarea id="note" name="note" rows="8" cols="50"></textarea></p>
  <p><button type="submit">Send the application</button></p>
</form>

<p class="small">
  What we do with your details is set out on the <a href="/privacy.php">privacy page</a>.
  The full list of openings is on <a href="/vacancies.php">the vacancies page</a>.
</p>
