<p class="crumbs">
  <a href="/">Home</a> &rsaquo; <a href="/news.php">News</a> &rsaquo;
  <?= bt_e((string) ($item['title'] ?? '')) ?>
</p>

<p class="small"><?= bt_e(bt_date((string) ($item['published_at'] ?? ''))) ?></p>

<?php if (trim((string) ($item['summary'] ?? '')) !== '') { ?>
  <p><strong><?= bt_e((string) ($item['summary'] ?? '')) ?></strong></p>
<?php } ?>

<?php
// The body is typed as plain text with a blank line between paragraphs, which is how the
// sales office has always sent it over.
foreach (preg_split("/\n\s*\n/", (string) ($item['body'] ?? '')) as $para) {
    if (trim((string) $para) === '') {
        continue;
    }
    ?>
    <p><?= bt_e(trim((string) $para)) ?></p>
    <?php
}
?>

<p class="small"><a href="/news.php">Back to the news</a></p>
