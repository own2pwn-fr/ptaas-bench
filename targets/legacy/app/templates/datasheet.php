<?php
// The datasheet shows whatever the buying office has filled in against the line. Fields
// left empty in the product record are simply left out rather than shown blank.
$bt_fields = [
    'Reference' => 'reference',
    'Description' => 'name',
    'Pack size' => 'pack_size',
    'Sold in' => 'unit',
    'Material' => 'material',
    'Finish' => 'finish',
    'Standard' => 'standard',
    'Thread' => 'thread',
    'Head' => 'head',
    'Drive' => 'drive',
    'Length' => 'length',
    'Diameter' => 'diameter',
    'Weight' => 'weight',
    'Country of origin' => 'origin',
];
?>
<p>
  Technical detail for this line as held by the buying office at Elland. Where a figure
  is quoted to a standard, the standard is named. Weights are for the pack unless the
  line is sold singly.
</p>

<table class="grid">
  <tbody>
  <?php foreach ($bt_fields as $label => $key) { ?>
    <?php if (trim((string) ($product[$key] ?? '')) !== '') { ?>
      <tr>
        <th><?= bt_e((string) $label) ?></th>
        <td><?= bt_e((string) ($product[$key] ?? '')) ?></td>
      </tr>
    <?php } ?>
  <?php } ?>
    <tr>
      <th>Price</th>
      <td class="num"><?= bt_e(bt_money((int) ($product['price_pence'] ?? 0))) ?> exclusive of VAT</td>
    </tr>
    <tr>
      <th>Stock at Elland</th>
      <td class="num"><?= (int) ($product['stock'] ?? 0) > 0 ? bt_e((string) (int) ($product['stock'] ?? 0)) : 'to order' ?></td>
    </tr>
  </tbody>
</table>

<?php if (trim((string) ($product['description'] ?? '')) !== '') { ?>
  <h2>Notes</h2>
  <p><?= bt_e((string) ($product['description'] ?? '')) ?></p>
<?php } ?>

<p class="small">
  Figures are given in good faith and may be revised by the manufacturer without notice.
  Where a job depends on a dimension, ask the trade desk for the manufacturer's own
  sheet before you order.
</p>

<ul class="plain">
  <li><a href="/product.php?ref=<?= bt_e(rawurlencode((string) ($product['reference'] ?? ''))) ?>">Back to <?= bt_e((string) ($product['name'] ?? '')) ?></a></li>
  <li><a href="/stock.php?ref=<?= bt_e(rawurlencode((string) ($product['reference'] ?? ''))) ?>">Stock at each depot</a></li>
</ul>
