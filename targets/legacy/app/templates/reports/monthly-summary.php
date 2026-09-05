<?php
// Orders and value by month, a rolling year.
try {
    $btMonths = bt_db_rows(
        "SELECT DATE_FORMAT(placed_at, '%Y-%m') AS month, COUNT(*) AS orders, SUM(total_pence) AS value_pence FROM orders GROUP BY month ORDER BY month DESC LIMIT 12",
    );
} catch (Throwable) {
    $btMonths = null;
}
?>
<div class="report-body">
  <h2>Monthly summary</h2>
  <?php if ($btMonths === null) { ?>
    <p>This report cannot be built at the moment. Try again after the overnight run.</p>
  <?php } elseif ($btMonths === []) { ?>
    <p>No orders on file to summarise.</p>
  <?php } else { ?>
    <table class="grid">
      <thead><tr><th>Month</th><th class="num">Orders</th><th class="num">Value</th><th class="num">Average order</th></tr></thead>
      <tbody>
      <?php foreach ($btMonths as $btRow) { ?>
        <?php $btOrders = (int) ($btRow['orders'] ?? 0); ?>
        <tr>
          <td><?= bt_e((string) ($btRow['month'] ?? '')) ?></td>
          <td class="num"><?= bt_e((string) $btOrders) ?></td>
          <td class="num"><?= bt_e(bt_money((int) ($btRow['value_pence'] ?? 0))) ?></td>
          <td class="num"><?= bt_e($btOrders > 0 ? bt_money((int) round(((int) ($btRow['value_pence'] ?? 0)) / $btOrders)) : '') ?></td>
        </tr>
      <?php } ?>
      </tbody>
    </table>
    <p class="small">Goods value only. Carriage and value added tax are excluded.</p>
  <?php } ?>
</div>
