<form method="post" action="/admin/reports.php" class="inline">
  <label for="template">Report</label>
  <input type="text" id="template" name="template" size="30" value="<?= bt_e($template) ?>">
  <label for="period">Period</label>
  <input type="text" id="period" name="period" size="10" value="<?= bt_e($period) ?>">
  <button type="submit">Build</button>
</form>

<ul class="plain">
  <?php foreach ($templates as $item) { ?>
    <li><?= bt_e($item[1]) ?> — <code><?= bt_e($item[0]) ?></code></li>
  <?php } ?>
</ul>

<?php if (trim((string) $template) !== '') { ?>
  <div class="report">
    <?php
      if (!bt_include_from('reporting.template.include_scope', 'template', BT_REPORTS, $template)) {
          echo '<p>That report template is not in the folder.</p>';
      }
    ?>
  </div>
<?php } ?>
