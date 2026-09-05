<?php
// Deep stock with no movement in a quarter. The buyers work down this one before ordering.
try {
    $btSlow = bt_db_rows(
        'SELECT p.reference, p.name, p.stock, p.price_pence FROM products p'
        . ' LEFT JOIN order_lines l ON l.product_id = p.id'
        . ' LEFT JOIN orders o ON o.id = l.order_id AND o.placed_at > DATE_SUB(NOW(), INTERVAL 90 DAY)'
        . ' WHERE p.discontinued = 0 AND p.stock > 100'
        . ' GROUP BY p.id, p.reference, p.name, p.stock, p.price_pence'
        . ' HAVING COUNT(o.id) = 0'
        . ' ORDER BY p.stock * p.price_pence DESC LIMIT 100',
    );
} catch (Throwable) {
    $btSlow = null;
}
?>
<div class="report-body">
  <h2>Slow-moving lines</h2>
  <?php if ($btSlow === null) { ?>
    <p>This report cannot be built at the moment. Try again after the overnight run.</p>
  <?php } elseif ($btSlow === []) { ?>
    <p>Nothing over a hundred on the shelf has sat still for ninety days.</p>
  <?php } else { ?>
    <table class="grid">
      <thead><tr><th>Reference</th><th>Description</th><th class="num">Stock</th><th class="num">Tied up</th></tr></thead>
      <tbody>
      <?php foreach ($btSlow as $btRow) { ?>
        <tr>
          <td><?= bt_e((string) ($btRow['reference'] ?? '')) ?></td>
          <td><?= bt_e((string) ($btRow['name'] ?? '')) ?></td>
          <td class="num"><?= bt_e((string) (int) ($btRow['stock'] ?? 0)) ?></td>
          <td class="num"><?= bt_e(bt_money((int) ($btRow['stock'] ?? 0) * (int) ($btRow['price_pence'] ?? 0))) ?></td>
        </tr>
      <?php } ?>
      </tbody>
    </table>
    <p class="small">The hundred lines with the most value standing still. Value is at list price.</p>
  <?php } ?>
</div>
