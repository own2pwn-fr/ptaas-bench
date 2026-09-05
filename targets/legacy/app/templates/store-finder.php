<p>
  Put in a postcode and we will show the depots in that part of the country. The match is
  on the postcode area, so it is a rough guide rather than a mileage; if two look equally
  close, ring both and take whichever has the line on the shelf.
</p>

<form method="post" action="/store-finder.php" class="inline">
  <label for="postcode">Postcode</label>
  <input type="text" id="postcode" name="postcode" size="10" value="<?= bt_e((string) $postcode) ?>">
  <button type="submit">Find a depot</button>
</form>

<?php if (trim((string) $postcode) === '') { ?>
  <p class="small">Nothing looked up yet. The full list is on <a href="/branches.php">the depots page</a>.</p>
<?php } elseif ($branches === []) { ?>
  <p>Nothing came back for <?= bt_e((string) $postcode) ?>. Try the first part of the postcode on its own, or use <a href="/branches.php">the depot list</a>.</p>
<?php } else { ?>
  <h2>Depots near <?= bt_e((string) $postcode) ?></h2>
  <table class="grid">
    <thead><tr><th>Depot</th><th>Town</th><th>Postcode</th><th>Telephone</th></tr></thead>
    <tbody>
    <?php foreach ($branches as $branch) { ?>
      <tr>
        <td><?= bt_e((string) ($branch['name'] ?? '')) ?></td>
        <td><?= bt_e((string) ($branch['town'] ?? '')) ?></td>
        <td><?= bt_e((string) ($branch['postcode'] ?? '')) ?></td>
        <td><?= bt_e((string) ($branch['phone'] ?? '')) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
  <p class="small">If none of these is convenient, we deliver on our own vans across the north of England. See <a href="/delivery.php">delivery</a>.</p>
<?php } ?>
