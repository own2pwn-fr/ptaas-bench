<?php
// Carriage charged out, by month, for the last half year.
try {
    $btCarriage = bt_db_rows(
        "SELECT DATE_FORMAT(placed_at, '%Y-%m') AS month, SUM(carriage_pence) AS carriage_pence, COUNT(*) AS orders FROM orders GROUP BY month ORDER BY month DESC LIMIT 6",
    );
} catch (Throwable) {
    $btCarriage = null;
}
?>
<div class="panel-body">
  <h3>Carriage spend</h3>
  <?php if ($btCarriage === null) { ?>
    <p class="small">The carriage figures are not available at the moment.</p>
  <?php } elseif ($btCarriage === []) { ?>
    <p class="small">No orders on file.</p>
  <?php } else { ?>
    <table class="grid">
      <thead><tr><th>Month</th><th class="num">Orders</th><th class="num">Carriage</th></tr></thead>
      <tbody>
      <?php foreach ($btCarriage as $btRow) { ?>
        <tr>
          <td><?= bt_e((string) ($btRow['month'] ?? '')) ?></td>
          <td class="num"><?= bt_e((string) (int) ($btRow['orders'] ?? 0)) ?></td>
          <td class="num"><?= bt_e(bt_money((int) ($btRow['carriage_pence'] ?? 0))) ?></td>
        </tr>
      <?php } ?>
      </tbody>
    </table>
  <?php } ?>
</div>
