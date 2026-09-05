<p>
  Eight trade counters and the central warehouse at Elland. Counters open at seven in the
  morning so that vans can be loaded before the first site starts, and four of them open
  on Saturday mornings. Order online and choose a depot and we will send word when it is
  picked and ready.
</p>

<?php if ($branches === []) { ?>
  <p>The depot list is unavailable at the moment. Ring the trade desk on 01422 000000.</p>
<?php } else { ?>
  <table class="grid">
    <thead><tr><th>Depot</th><th>Town</th><th>Postcode</th><th>Telephone</th></tr></thead>
    <tbody>
    <?php foreach ($branches as $branch) { ?>
      <tr>
        <td><a href="/branch.php?id=<?= bt_e(rawurlencode((string) ($branch['id'] ?? ''))) ?>"><?= bt_e((string) ($branch['name'] ?? '')) ?></a></td>
        <td><?= bt_e((string) ($branch['town'] ?? '')) ?></td>
        <td><?= bt_e((string) ($branch['postcode'] ?? '')) ?></td>
        <td><?= bt_e((string) ($branch['phone'] ?? '')) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
<?php } ?>

<p class="small">
  Not sure which is nearest? <a href="/store-finder.php">Put your postcode in</a>.
</p>
