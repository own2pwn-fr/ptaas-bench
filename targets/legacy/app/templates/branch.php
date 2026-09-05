<p class="crumbs">
  <a href="/">Home</a> &rsaquo; <a href="/branches.php">Depots</a> &rsaquo;
  <?= bt_e((string) ($branch['name'] ?? '')) ?>
</p>

<div class="two-column">
  <div class="main">
    <table class="grid">
      <tbody>
        <tr><th>Town</th><td><?= bt_e((string) ($branch['town'] ?? '')) ?></td></tr>
        <tr><th>Postcode</th><td><?= bt_e((string) ($branch['postcode'] ?? '')) ?></td></tr>
        <tr><th>Telephone</th><td><?= bt_e((string) ($branch['phone'] ?? '')) ?></td></tr>
        <?php if (trim((string) ($branch['opening'] ?? '')) !== '') { ?>
          <tr><th>Counter hours</th><td><?= bt_e((string) ($branch['opening'] ?? '')) ?></td></tr>
        <?php } ?>
        <?php if (trim((string) ($branch['manager'] ?? '')) !== '') { ?>
          <tr><th>Depot manager</th><td><?= bt_e((string) ($branch['manager'] ?? '')) ?></td></tr>
        <?php } ?>
      </tbody>
    </table>

    <p>
      The counter carries the fast-moving fixings, abrasives, blades and site consumables.
      Anything else comes off the overnight trunk from Elland and is on the counter by
      half past seven the following morning if it is ordered before four o'clock.
    </p>

    <p>
      There is parking for vans at the front and a loading bay at the side for pallets.
      Collections are handed over against a signature; bring the order number or the name
      the order was placed in.
    </p>
  </div>

  <div class="side">
    <h2>At this depot</h2>
    <ul class="plain">
      <li><a href="/services.php">Studding cut to length and hose made up</a></li>
      <li><a href="/stock.php">Check stock before you set off</a></li>
      <li><a href="/delivery.php">Delivery and collection</a></li>
      <li><a href="/branches.php">All eight depots</a></li>
      <li><a href="/store-finder.php">Find the nearest depot</a></li>
    </ul>
    <p class="small">Depot number <?= bt_e((string) ($branch['id'] ?? '')) ?>. Quote it if you ring head office about a collection.</p>
  </div>
</div>
