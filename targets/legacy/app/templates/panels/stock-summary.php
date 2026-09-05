<?php
// Stock summary. Elland figures only; the depot counts come off the picking rounds.
try {
    $btStock = bt_db_row(
        'SELECT COUNT(*) AS line_count, SUM(stock = 0) AS out_of_stock, SUM(stock * price_pence) AS value_pence FROM products WHERE discontinued = 0',
    );
} catch (Throwable) {
    $btStock = null;
}
?>
<div class="panel-body">
  <h3>Stock summary</h3>
  <?php if ($btStock === null) { ?>
    <p class="small">The stock figures are not available at the moment.</p>
  <?php } else { ?>
    <table class="grid">
      <tbody>
        <tr><th>Lines listed</th><td class="num"><?= bt_e((string) (int) ($btStock['line_count'] ?? 0)) ?></td></tr>
        <tr><th>Off the shelf</th><td class="num"><?= bt_e((string) (int) ($btStock['out_of_stock'] ?? 0)) ?></td></tr>
        <tr><th>Value on the shelf</th><td class="num"><?= bt_e(bt_money((int) ($btStock['value_pence'] ?? 0))) ?></td></tr>
      </tbody>
    </table>
    <p class="small">Withdrawn lines are left out. Value is at list price, not at cost.</p>
  <?php } ?>
</div>
