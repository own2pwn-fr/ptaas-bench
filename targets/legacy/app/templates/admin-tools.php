<p>How much of the attachment volume each folder is using.</p>

<form method="post" action="/admin/tools.php" class="inline">
  <label for="folder">Folder</label>
  <input type="text" id="folder" name="folder" size="24" value="<?= bt_e($folder) ?>">
  <button type="submit">Run</button>
</form>

<p class="small">Folders in use: <?= bt_e(implode(', ', $folders)) ?></p>

<?php if ($report !== null) { ?>
  <h2>Result</h2>
  <pre class="output"><?= bt_e($report['shown']) ?></pre>
<?php } ?>
