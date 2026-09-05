<?php
// The basket itself travels back in the form below rather than being read from the
// session alone. It was done that way in 2010, when the site went on to two web servers
// and a customer whose session landed on the other machine lost the lot between the
// listing and the checkout; carrying it in the post means the basket survives a session
// that has gone missing.
?>
<?php if ($lines === []) { ?>
  <p>
    Your basket is empty. Add a line from <a href="/catalogue.php">the catalogue</a>, or
    put a reference straight into the quick reference box at the top of the page.
  </p>
<?php } else { ?>
  <table class="grid">
    <thead><tr><th>Reference</th><th>Description</th><th>Unit</th><th class="num">Price</th><th class="num">Quantity</th><th class="num">Line total</th></tr></thead>
    <tbody>
    <?php foreach ($lines as $line) { ?>
      <?php $product = $line['product'] ?? []; ?>
      <tr>
        <td><a href="/product.php?ref=<?= bt_e(rawurlencode((string) ($product['reference'] ?? ''))) ?>"><?= bt_e((string) ($product['reference'] ?? '')) ?></a></td>
        <td><?= bt_e((string) ($product['name'] ?? '')) ?></td>
        <td><?= bt_e((string) ($product['unit'] ?? '')) ?></td>
        <td class="num"><?= bt_e(bt_money((int) ($product['price_pence'] ?? 0))) ?></td>
        <td class="num"><?= bt_e((string) (int) ($line['quantity'] ?? 0)) ?></td>
        <td class="num"><?= bt_e(bt_money((int) ($line['subtotal'] ?? 0))) ?></td>
      </tr>
    <?php } ?>
    </tbody>
    <tfoot>
      <tr>
        <th colspan="5">Goods total, exclusive of VAT and carriage</th>
        <td class="num"><?= bt_e(bt_money((int) $total)) ?></td>
      </tr>
    </tfoot>
  </table>
<?php } ?>

<div class="panel">
  <h2>Collection depot</h2>
  <p>
    Choose where you would like the order picked. Leave it as it is and the order goes on
    the van run from Elland.
    <?php if (trim((string) ($basket->deliveryBranch ?? '')) !== '') { ?>
      At present it is set to <strong><?= bt_e((string) ($basket->deliveryBranch ?? '')) ?></strong>.
    <?php } ?>
  </p>

  <form method="post" action="/cart.php" class="inline">
    <!-- the basket goes back with the form so it survives a session lost between the servers -->
    <input type="hidden" name="basket" value="<?= bt_e(base64_encode(serialize($basket))) ?>">
    <input type="hidden" name="action" value="branch">
    <label for="branch">Depot</label>
    <select id="branch" name="branch">
      <?php foreach ($branches as $row) { ?>
        <option value="<?= bt_e((string) ($row['name'] ?? '')) ?>"<?= (string) ($basket->deliveryBranch ?? '') === (string) ($row['name'] ?? '') ? ' selected="selected"' : '' ?>><?= bt_e((string) ($row['name'] ?? '')) ?></option>
      <?php } ?>
    </select>
    <button type="submit">Set the depot</button>
  </form>
</div>

<form method="post" action="/cart.php" class="inline">
  <input type="hidden" name="action" value="clear">
  <button type="submit">Empty the basket</button>
</form>

<ul class="plain">
  <li><a href="/catalogue.php">Carry on adding lines</a></li>
  <li><a href="/quote.php">Ask the trade desk to price the lot</a></li>
  <li><a href="/delivery.php">Carriage and delivery times</a></li>
</ul>
