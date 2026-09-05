<?php
// Order count and value by depot, whole history.
try {
    $btDepots = bt_db_rows(
        'SELECT b.name, b.town, COUNT(o.id) AS orders, SUM(o.total_pence) AS value_pence FROM orders o JOIN branches b ON b.id = o.branch_id GROUP BY b.id, b.name, b.town ORDER BY value_pence DESC',
    );
} catch (Throwable) {
    $btDepots = null;
}
?>
<div class="report-body">
  <h2>Depot margins</h2>
  <?php if ($btDepots === null) { ?>
    <p>This report cannot be built at the moment. Try again after the overnight run.</p>
  <?php } elseif ($btDepots === []) { ?>
    <p>Nothing has been booked against a depot yet.</p>
  <?php } else { ?>
    <table class="grid">
      <thead><tr><th>Depot</th><th>Town</th><th class="num">Orders</th><th class="num">Value</th><th class="num">Average order</th></tr></thead>
      <tbody>
      <?php foreach ($btDepots as $btRow) { ?>
        <?php $btOrders = (int) ($btRow['orders'] ?? 0); ?>
        <tr>
          <td><?= bt_e((string) ($btRow['name'] ?? '')) ?></td>
          <td><?= bt_e((string) ($btRow['town'] ?? '')) ?></td>
          <td class="num"><?= bt_e((string) $btOrders) ?></td>
          <td class="num"><?= bt_e(bt_money((int) ($btRow['value_pence'] ?? 0))) ?></td>
          <td class="num"><?= bt_e($btOrders > 0 ? bt_money((int) round(((int) ($btRow['value_pence'] ?? 0)) / $btOrders)) : '') ?></td>
        </tr>
      <?php } ?>
      </tbody>
    </table>
    <p class="small">Orders raised at Elland for direct delivery are counted against Elland.</p>
  <?php } ?>
</div>
