<?php
// The copy is plain text held in copy.php; everything is escaped on the way out, so no
// tags belong in those strings.
?>
<?php if (trim((string) $strapline) !== '') { ?>
  <p class="strapline"><?= bt_e((string) $strapline) ?></p>
<?php } ?>

<?php if ($sections === []) { ?>
  <p>There is nothing on this page at the moment.</p>
<?php } else { ?>
  <?php foreach ($sections as $section) { ?>
    <h2><?= bt_e((string) ($section['heading'] ?? '')) ?></h2>
    <?php foreach (($section['body'] ?? []) as $para) { ?>
      <p><?= bt_e((string) $para) ?></p>
    <?php } ?>
  <?php } ?>
<?php } ?>

<p class="small">
  Anything here that does not answer your question, ring the trade desk on 01422 000000 or
  use <a href="/contact.php">the enquiry form</a>.
</p>
