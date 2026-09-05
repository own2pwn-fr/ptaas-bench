<p class="crumbs">
  <a href="/">Home</a> &rsaquo; <a href="/vacancies.php">Vacancies</a> &rsaquo;
  <?= bt_e((string) ($item['title'] ?? '')) ?>
</p>

<table class="grid">
  <tbody>
    <tr><th>Based at</th><td><?= bt_e((string) ($item['location'] ?? '')) ?></td></tr>
    <tr><th>Closing date</th><td><?= bt_e(bt_date((string) ($item['closes_at'] ?? ''))) ?></td></tr>
  </tbody>
</table>

<?php
// The wording comes from the personnel office as plain text, one blank line between
// paragraphs.
foreach (preg_split("/\n\s*\n/", (string) ($item['body'] ?? '')) as $para) {
    if (trim((string) $para) === '') {
        continue;
    }
    ?>
    <p><?= bt_e(trim((string) $para)) ?></p>
    <?php
}
?>

<div class="panel">
  <h2>How to apply</h2>
  <p>
    Use <a href="/careers-apply.php">the application form</a> and pick this role from the
    list, or post a curriculum vitae to the personnel office at Elland. We reply to
    everyone who applies, though it can take a fortnight in a busy period.
  </p>
</div>

<p class="small"><a href="/vacancies.php">Back to the vacancies</a></p>
