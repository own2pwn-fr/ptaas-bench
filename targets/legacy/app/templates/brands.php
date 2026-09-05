<p>
  The makes we hold as stock lines. Most of them we have bought from for years, and the
  buying office at Elland deals with them direct rather than through a factor, which is
  why we can usually get a special out of them inside a week.
</p>

<?php if ($brands === []) { ?>
  <p>The brand pages are being rebuilt. Everything we stock is in <a href="/catalogue.php">the catalogue</a>.</p>
<?php } else { ?>
  <ul class="tiles">
  <?php foreach ($brands as $brand) { ?>
    <li>
      <a href="/brand.php?slug=<?= bt_e(rawurlencode((string) ($brand['slug'] ?? ''))) ?>"><?= bt_e((string) ($brand['name'] ?? '')) ?></a>
      <span class="small"><?= bt_e((string) ($brand['blurb'] ?? '')) ?></span>
    </li>
  <?php } ?>
  </ul>
<?php } ?>

<p class="small">
  A make you cannot see here is not necessarily one we cannot get. Ask the trade desk on
  01422 000000 or use <a href="/contact.php">the enquiry form</a>.
</p>
