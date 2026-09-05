<?php
// Orders taken against each depot over the last thirty days.
try {
    $btThroughput = bt_db_rows(
        'SELECT b.name, COUNT(o.id) AS orders FROM branches b LEFT JOIN orders o ON o.branch_id = b.id AND o.placed_at > DATE_SUB(NOW(), INTERVAL 30 DAY) GROUP BY b.id, b.name ORDER BY orders DESC',
    );
} catch (Throwable) {
    $btThroughput = null;
}
?>
<div class="panel-body">
  <h3>Depot throughput</h3>
  <?php if ($btThroughput === null) { ?>
    <p class="small">The depot figures are not available at the moment.</p>
  <?php } elseif ($btThroughput === []) { ?>
    <p class="small">No depots on file.</p>
  <?php } else { ?>
    <table class="grid">
      <thead><tr><th>Depot</th><th class="num">Orders, 30 days</th></tr></thead>
      <tbody>
      <?php foreach ($btThroughput as $btRow) { ?>
        <tr>
          <td><?= bt_e((string) ($btRow['name'] ?? '')) ?></td>
          <td class="num"><?= bt_e((string) (int) ($btRow['orders'] ?? 0)) ?></td>
        </tr>
      <?php } ?>
      </tbody>
    </table>
  <?php } ?>
</div>
