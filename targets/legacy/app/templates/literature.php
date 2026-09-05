<p>
  The printed catalogue, the section leaflets and the manufacturers' sheets we are asked
  for most often. Everything here is a PDF. The main catalogue is a large file and is
  better collected from a counter on a disc if you are on a dial-up line.
</p>

<?php
// This page was moved onto the template engine during the rebuild, like the questions
// page. The work was stopped part-way through, so the native version below is still what
// renders anywhere the engine is not installed, and the two have to look the same.
$btRendered = bt_twig('literature.html.twig', ['documents' => $documents]);
if ($btRendered !== '') {
    echo $btRendered;
} else {
?>
<?php if ($documents === []) { ?>
  <p>The library is empty at the moment. Ask at any counter for a printed copy.</p>
<?php } else { ?>
  <table class="grid">
    <thead><tr><th>Title</th><th>File</th><th class="num">Pages</th><th>Issued</th></tr></thead>
    <tbody>
    <?php foreach ($documents as $document) { ?>
      <tr>
        <td><a href="/download.php?doc=<?= bt_e(rawurlencode((string) ($document['filename'] ?? ''))) ?>"><?= bt_e((string) ($document['title'] ?? '')) ?></a></td>
        <td class="small"><?= bt_e((string) ($document['filename'] ?? '')) ?></td>
        <td class="num"><?= bt_e((string) (int) ($document['pages'] ?? 0)) ?></td>
        <td><?= bt_e(bt_date((string) ($document['published_at'] ?? ''))) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
<?php } ?>
<?php } ?>

<p class="small">
  A new printed catalogue is issued every January. To be put on the mailing list, use
  <a href="/contact.php">the enquiry form</a> or ask at a counter.
</p>
