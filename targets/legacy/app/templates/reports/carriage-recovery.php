<?php
// What we charged for carriage against what the hauliers charged us.
try {
    $btRecovery = bt_db_rows(
        "SELECT DATE_FORMAT(placed_at, '%Y-%m') AS month, SUM(carriage_pence) AS charged_pence, SUM(carriage_cost_pence) AS cost_pence FROM orders GROUP BY month ORDER BY month DESC LIMIT 12",
    );
} catch (Throwable) {
    $btRecovery = null;
}
?>
<div class="report-body">
  <h2>Carriage recovery</h2>
  <?php if ($btRecovery === null) { ?>
    <p>This report cannot be built at the moment. Try again after the overnight run.</p>
  <?php } elseif ($btRecovery === []) { ?>
    <p>No orders on file to compare.</p>
  <?php } else { ?>
    <table class="grid">
      <thead><tr><th>Month</th><th class="num">Charged</th><th class="num">Paid out</th><th class="num">Difference</th></tr></thead>
      <tbody>
      <?php foreach ($btRecovery as $btRow) { ?>
        <?php
          $btCharged = (int) ($btRow['charged_pence'] ?? 0);
          $btCost = (int) ($btRow['cost_pence'] ?? 0);
        ?>
        <tr>
          <td><?= bt_e((string) ($btRow['month'] ?? '')) ?></td>
          <td class="num"><?= bt_e(bt_money($btCharged)) ?></td>
          <td class="num"><?= bt_e(bt_money($btCost)) ?></td>
          <td class="num"><?= bt_e(bt_money($btCharged - $btCost)) ?></td>
        </tr>
      <?php } ?>
      </tbody>
    </table>
    <p class="small">A negative difference is carriage given away, mostly on orders over the free-carriage figure.</p>
  <?php } ?>
</div>
