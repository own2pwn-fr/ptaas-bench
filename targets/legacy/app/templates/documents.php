<?php if ($uploaded !== '') { ?>
  <p class="notice"><?= bt_e($uploaded) ?> has been filed against the account.</p>
<?php } ?>

<p>
  Signed delivery notes, photographs from site and any certificates we have asked for.
  Send them in whatever form you have them; the name they arrive with is kept for the
  audit trail.
</p>

<form method="post" action="/account/documents.php" enctype="multipart/form-data" class="stacked">
  <p><label for="attachment">File</label><input type="file" id="attachment" name="attachment"></p>
  <p><label for="note">Note</label><input type="text" id="note" name="note" size="50"></p>
  <p><button type="submit">Send it in</button></p>
</form>

<?php if ($documents === []) { ?>
  <p class="small">Nothing filed yet.</p>
<?php } else { ?>
  <table class="grid">
    <thead><tr><th>File</th><th>Note</th><th>Filed</th><th>Size</th></tr></thead>
    <tbody>
    <?php foreach ($documents as $document) { ?>
      <tr>
        <td><a href="/uploads/<?= bt_e(rawurlencode((string) $document['filename'])) ?>"><?= bt_e((string) $document['filename']) ?></a></td>
        <td><?= bt_e((string) $document['note']) ?></td>
        <td><?= bt_e(bt_date((string) $document['uploaded_at'])) ?></td>
        <td><a href="/account/documents.php?preview=<?= bt_e(rawurlencode((string) $document['filename'])) ?>">check</a></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
<?php } ?>

<?php if ($report !== null) { ?>
  <h2>File check</h2>
  <pre class="output"><?= bt_e($report['shown']) ?></pre>
<?php } ?>
