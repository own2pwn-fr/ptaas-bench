<p>
  New lines, depot openings, holiday arrangements and anything else the counters think
  you ought to know. The same items go out in <a href="/newsletter.php">the monthly
  newsletter</a>.
</p>

<?php if ($items === []) { ?>
  <p>There is no news at the moment.</p>
<?php } else { ?>
  <ul class="plain">
  <?php foreach ($items as $item) { ?>
    <li>
      <h2><a href="/news-item.php?slug=<?= bt_e(rawurlencode((string) ($item['slug'] ?? ''))) ?>"><?= bt_e((string) ($item['title'] ?? '')) ?></a></h2>
      <p class="small"><?= bt_e(bt_date((string) ($item['published_at'] ?? ''))) ?></p>
      <p><?= bt_e((string) ($item['summary'] ?? '')) ?></p>
    </li>
  <?php } ?>
  </ul>
<?php } ?>
