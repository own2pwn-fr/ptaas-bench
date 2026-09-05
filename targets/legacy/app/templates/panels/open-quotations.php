<?php
// Quotations the trade desk has priced and not yet turned into an order.
try {
    $btQuotes = bt_db_rows(
        "SELECT q.id, q.reference, q.created_at, c.company FROM quotes q JOIN customers c ON c.id = q.customer_id WHERE q.status = 'open' ORDER BY q.created_at DESC LIMIT 15",
    );
} catch (Throwable) {
    $btQuotes = null;
}
?>
<div class="panel-body">
  <h3>Open quotations</h3>
  <?php if ($btQuotes === null) { ?>
    <p class="small">The quotation list is not available at the moment.</p>
  <?php } elseif ($btQuotes === []) { ?>
    <p class="small">Nothing outstanding. Everything priced has been taken or let go.</p>
  <?php } else { ?>
    <table class="grid">
      <thead><tr><th>Reference</th><th>Company</th><th>Raised</th></tr></thead>
      <tbody>
      <?php foreach ($btQuotes as $btRow) { ?>
        <tr>
          <td><?= bt_e((string) ($btRow['reference'] ?? '')) ?></td>
          <td><?= bt_e((string) ($btRow['company'] ?? '')) ?></td>
          <td><?= bt_e(bt_date((string) ($btRow['created_at'] ?? ''))) ?></td>
        </tr>
      <?php } ?>
      </tbody>
    </table>
    <p class="small">The fifteen most recent. Quotations hold their price for thirty days.</p>
  <?php } ?>
</div>
