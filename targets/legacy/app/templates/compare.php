<?php
// Up to four references are put side by side. The rows are fixed so that a gap in one
// column lines up with the same field in the others.
$bt_rows = [
    'Description' => 'name',
    'Pack size' => 'pack_size',
    'Sold in' => 'unit',
    'Material' => 'material',
    'Finish' => 'finish',
    'Standard' => 'standard',
];
?>
<?php if ($products === []) { ?>
  <p>
    Nothing has been picked out to compare. Build a comparison by listing the references
    after the address, separated by commas, like this:
    <code>/compare.php?refs=BT-1042,BT-1043</code>. Four is the most it will show at once.
  </p>
  <p>
    The quickest way to collect the codes is from the printed catalogue, or from the
    <a href="/catalogue.php">catalogue listing</a> where each line carries its reference.
  </p>
<?php } else { ?>
  <p>Side by side, in the order you gave the references. Prices exclude value added tax.</p>

  <table class="grid">
    <thead>
      <tr>
        <th>Field</th>
        <?php foreach ($products as $product) { ?>
          <th><a href="/product.php?ref=<?= bt_e(rawurlencode((string) ($product['reference'] ?? ''))) ?>"><?= bt_e((string) ($product['reference'] ?? '')) ?></a></th>
        <?php } ?>
      </tr>
    </thead>
    <tbody>
      <?php foreach ($bt_rows as $label => $key) { ?>
        <tr>
          <th><?= bt_e((string) $label) ?></th>
          <?php foreach ($products as $product) { ?>
            <td><?= bt_e((string) ($product[$key] ?? '')) ?></td>
          <?php } ?>
        </tr>
      <?php } ?>
      <tr>
        <th>Price</th>
        <?php foreach ($products as $product) { ?>
          <td class="num"><?= bt_e(bt_money((int) ($product['price_pence'] ?? 0))) ?></td>
        <?php } ?>
      </tr>
      <tr>
        <th>Stock at Elland</th>
        <?php foreach ($products as $product) { ?>
          <td class="num"><?= (int) ($product['stock'] ?? 0) > 0 ? bt_e((string) (int) ($product['stock'] ?? 0)) : 'to order' ?></td>
        <?php } ?>
      </tr>
      <tr>
        <th>Datasheet</th>
        <?php foreach ($products as $product) { ?>
          <td><a href="/product-datasheet.php?ref=<?= bt_e(rawurlencode((string) ($product['reference'] ?? ''))) ?>">Detail</a></td>
        <?php } ?>
      </tr>
    </tbody>
  </table>

  <p class="small">Add another reference to the list after the address to bring a fourth column in.</p>
<?php } ?>
