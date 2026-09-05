<?php
// The section and the sort are carried through the paging links so that stepping to the
// next page does not quietly drop the customer back into the whole catalogue.
$bt_keep = '';
if ((string) $section !== '') {
    $bt_keep .= '&amp;section=' . bt_e(rawurlencode((string) $section));
}
if ((string) $sort !== '') {
    $bt_keep .= '&amp;sort=' . bt_e(rawurlencode((string) $sort));
}
$bt_sorts = [
    'name' => 'Description, A to Z',
    'price' => 'Price, lowest first',
    'price-desc' => 'Price, highest first',
    'reference' => 'Reference',
    'newest' => 'Newest lines first',
];
?>
<p class="crumbs"><a href="/">Home</a> &rsaquo; Catalogue</p>

<div class="two-column">
  <div class="side">
    <h2>Sections</h2>
    <ul class="plain">
      <li><a href="/catalogue.php">Everything (<?= bt_e((string) (int) $total) ?> lines)</a></li>
      <?php foreach ($sections as $row) { ?>
        <li><a href="/catalogue.php?section=<?= bt_e(rawurlencode((string) ($row['slug'] ?? ''))) ?>"><?= bt_e((string) ($row['name'] ?? '')) ?></a></li>
      <?php } ?>
    </ul>
    <p class="small">Prices are exclusive of value added tax and are the list prices. Account customers see their own terms once signed in.</p>
  </div>

  <div class="main">
    <form method="get" action="/catalogue.php" class="inline">
      <?php if ((string) $section !== '') { ?>
        <input type="hidden" name="section" value="<?= bt_e((string) $section) ?>">
      <?php } ?>
      <label for="sort">Order by</label>
      <select id="sort" name="sort">
        <?php foreach ($bt_sorts as $key => $label) { ?>
          <option value="<?= bt_e((string) $key) ?>"<?= (string) $sort === (string) $key ? ' selected="selected"' : '' ?>><?= bt_e($label) ?></option>
        <?php } ?>
      </select>
      <button type="submit">Sort</button>
    </form>

    <p class="small">
      <?= bt_e((string) (int) $total) ?> line(s), page <?= bt_e((string) (int) ($window['page'] ?? 1)) ?>
      of <?= bt_e((string) (int) ($window['pages'] ?? 1)) ?>.
    </p>

    <?php if ($products === []) { ?>
      <p>There is nothing in that section at the moment. Try another section, or ring the trade desk on 01422 000000.</p>
    <?php } else { ?>
      <table class="grid">
        <thead><tr><th>Reference</th><th>Description</th><th>Brand</th><th class="num">Price</th><th>Unit</th><th class="num">Stock</th></tr></thead>
        <tbody>
        <?php foreach ($products as $product) { ?>
          <tr>
            <td><a href="/product.php?ref=<?= bt_e(rawurlencode((string) ($product['reference'] ?? ''))) ?>"><?= bt_e((string) ($product['reference'] ?? '')) ?></a></td>
            <td><?= bt_e((string) ($product['name'] ?? '')) ?></td>
            <td><?= bt_e((string) ($product['brand'] ?? '')) ?></td>
            <td class="num"><?= bt_e(bt_money((int) ($product['price_pence'] ?? 0))) ?></td>
            <td><?= bt_e((string) ($product['unit'] ?? '')) ?></td>
            <td class="num"><?= (int) ($product['stock'] ?? 0) > 0 ? bt_e((string) (int) ($product['stock'] ?? 0)) : 'to order' ?></td>
          </tr>
        <?php } ?>
        </tbody>
      </table>

      <p class="inline">
        <?php if ((int) ($window['page'] ?? 1) > 1) { ?>
          <a href="/catalogue.php?page=<?= bt_e((string) ((int) ($window['page'] ?? 1) - 1)) ?><?= $bt_keep ?>">Previous page</a>
        <?php } else { ?>
          <span class="small">Previous page</span>
        <?php } ?>
        &nbsp;
        <?php if ((int) ($window['page'] ?? 1) < (int) ($window['pages'] ?? 1)) { ?>
          <a href="/catalogue.php?page=<?= bt_e((string) ((int) ($window['page'] ?? 1) + 1)) ?><?= $bt_keep ?>">Next page</a>
        <?php } else { ?>
          <span class="small">Next page</span>
        <?php } ?>
      </p>
    <?php } ?>
  </div>
</div>
