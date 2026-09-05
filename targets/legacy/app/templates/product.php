<p class="crumbs">
  <a href="/">Home</a> &rsaquo; <a href="/catalogue.php">Catalogue</a>
  <?php if (trim((string) ($product['section_slug'] ?? '')) !== '') { ?>
    &rsaquo; <a href="/category.php?slug=<?= bt_e(rawurlencode((string) ($product['section_slug'] ?? ''))) ?>"><?= bt_e((string) ($product['section'] ?? '')) ?></a>
  <?php } ?>
  &rsaquo; <?= bt_e((string) ($product['reference'] ?? '')) ?>
</p>

<div class="two-column">
  <div class="main">
    <p class="small">
      Reference <?= bt_e((string) ($product['reference'] ?? '')) ?>
      <?php if (trim((string) ($product['brand'] ?? '')) !== '') { ?>
        &middot; <?= bt_e((string) ($product['brand'] ?? '')) ?>
      <?php } ?>
    </p>

    <?php if (trim((string) ($product['description'] ?? '')) !== '') { ?>
      <p><?= bt_e((string) ($product['description'] ?? '')) ?></p>
    <?php } ?>

    <table class="grid">
      <tbody>
        <tr><th>Price</th><td class="num"><?= bt_e(bt_money((int) ($product['price_pence'] ?? 0))) ?> each, exclusive of VAT</td></tr>
        <?php if ((int) ($product['was_pence'] ?? 0) > (int) ($product['price_pence'] ?? 0)) { ?>
          <tr><th>Was</th><td class="num"><?= bt_e(bt_money((int) ($product['was_pence'] ?? 0))) ?></td></tr>
        <?php } ?>
        <tr><th>Sold in</th><td><?= bt_e((string) ($product['unit'] ?? '')) ?></td></tr>
        <?php if (trim((string) ($product['pack_size'] ?? '')) !== '') { ?>
          <tr><th>Pack size</th><td><?= bt_e((string) ($product['pack_size'] ?? '')) ?></td></tr>
        <?php } ?>
        <tr>
          <th>Stock at Elland</th>
          <td class="num">
            <?php if ((int) ($product['stock'] ?? 0) > 0) { ?>
              <?= bt_e((string) (int) ($product['stock'] ?? 0)) ?> on the shelf
            <?php } else { ?>
              None on the shelf
            <?php } ?>
          </td>
        </tr>
      </tbody>
    </table>

    <form method="post" action="/cart-add.php" class="inline">
      <input type="hidden" name="ref" value="<?= bt_e((string) ($product['reference'] ?? '')) ?>">
      <label for="qty">Quantity</label>
      <input type="text" id="qty" name="qty" size="4" value="1">
      <button type="submit">Add to basket</button>
    </form>

    <ul class="plain">
      <li><a href="/stock.php?ref=<?= bt_e(rawurlencode((string) ($product['reference'] ?? ''))) ?>">Stock at each depot</a></li>
      <li><a href="/product-datasheet.php?ref=<?= bt_e(rawurlencode((string) ($product['reference'] ?? ''))) ?>">Datasheet for this line</a></li>
      <li><a href="/quote.php">Ask the trade desk to price a quantity</a></li>
    </ul>

    <?php if ((int) ($product['stock'] ?? 0) === 0) { ?>
      <div class="panel">
        <h2>Tell me when it is back</h2>
        <p>
          This line is off the shelf at Elland. Leave an address and we will write to you
          when the next delivery is booked in. It does not commit you to anything.
        </p>
        <form method="post" action="/stock-alert.php" class="inline">
          <input type="hidden" name="ref" value="<?= bt_e((string) ($product['reference'] ?? '')) ?>">
          <label for="alert-email">E-mail</label>
          <input type="text" id="alert-email" name="email" size="30">
          <button type="submit">Let me know</button>
        </form>
      </div>
    <?php } ?>
  </div>

  <div class="side">
    <h2>Others in this section</h2>
    <?php if ($related === []) { ?>
      <p class="small">Nothing else is listed alongside this one.</p>
    <?php } else { ?>
      <ul class="plain">
      <?php foreach ($related as $row) { ?>
        <li>
          <a href="/product.php?ref=<?= bt_e(rawurlencode((string) ($row['reference'] ?? ''))) ?>"><?= bt_e((string) ($row['name'] ?? '')) ?></a>
          <span class="num"><?= bt_e(bt_money((int) ($row['price_pence'] ?? 0))) ?></span>
        </li>
      <?php } ?>
      </ul>
    <?php } ?>
    <p class="small">
      Put two or more references side by side with
      <a href="/compare.php">the comparison page</a>.
    </p>
  </div>
</div>
