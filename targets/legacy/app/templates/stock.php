<p>Type a reference to see what is on the shelf at each depot. Stock is as at the last picking round.</p>

<form method="get" action="/stock.php" class="inline">
  <label for="ref">Reference</label>
  <!-- the reference is put back in the box so a typo can be corrected without retyping -->
  <input type="text" id="ref" name="ref" size="16" value="<?= bt_out('stock.lookup.markup_escape', 'ref', $ref, 'attribute') ?>">
  <button type="submit">Check again</button>
</form>

<?php if (trim($ref) === '') { ?>
  <p class="small">Nothing checked yet.</p>
<?php } elseif ($rows === []) { ?>
  <p>No stock recorded against <?= bt_e($ref) ?>. It may be a special order line.</p>
<?php } else { ?>
  <table class="grid">
    <thead><tr><th>Depot</th><th>Town</th><th class="num">On the shelf</th></tr></thead>
    <tbody>
    <?php foreach ($rows as $row) { ?>
      <tr>
        <td><?= bt_e((string) $row['branch']) ?></td>
        <td><?= bt_e((string) $row['town']) ?></td>
        <td class="num"><?= bt_e((string) $row['quantity']) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
<?php } ?>
