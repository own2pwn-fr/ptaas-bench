<p>
  The questions the trade desk is asked most often. If yours is not here, there is more
  under <a href="/help.php">help</a>, or ring 01422 000000 and ask.
</p>

<?php
// This page was moved onto the template engine during the rebuild. The work was stopped
// part-way through, so the native version below is still what renders anywhere the
// engine is not installed, and the two have to look the same.
$btRendered = bt_twig('faq.html.twig', ['items' => $items]);
if ($btRendered !== '') {
    echo $btRendered;
} else {
?>
<?php if ($items === []) { ?>
  <p>There is nothing on this page at the moment.</p>
<?php } else { ?>
  <?php foreach ($items as $item) { ?>
    <div class="panel">
      <h2><?= bt_e((string) ($item[0] ?? '')) ?></h2>
      <p><?= bt_e((string) ($item[1] ?? '')) ?></p>
    </div>
  <?php } ?>
<?php } ?>
<?php } ?>

<p class="small">
  Still stuck? Use <a href="/contact.php">the enquiry form</a> or
  <a href="/callback.php">ask us to ring you</a>.
</p>
