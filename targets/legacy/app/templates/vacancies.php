<p>
  What we have open at the moment, at the depots and at Elland. Most of our vacancies are
  trade counter assistants, warehouse operatives and multi-drop drivers, and a fair number
  of our counter managers started as Saturday warehouse hands.
</p>

<?php if ($items === []) { ?>
  <p>
    Nothing is open at the moment. Send your details anyway through
    <a href="/careers-apply.php">the application form</a>; they are kept on file for six
    months and the counters take people on through the year.
  </p>
<?php } else { ?>
  <table class="grid">
    <thead><tr><th>Role</th><th>Based at</th><th>Closes</th></tr></thead>
    <tbody>
    <?php foreach ($items as $item) { ?>
      <tr>
        <td><a href="/vacancy.php?slug=<?= bt_e(rawurlencode((string) ($item['slug'] ?? ''))) ?>"><?= bt_e((string) ($item['title'] ?? '')) ?></a></td>
        <td><?= bt_e((string) ($item['location'] ?? '')) ?></td>
        <td><?= bt_e(bt_date((string) ($item['closes_at'] ?? ''))) ?></td>
      </tr>
    <?php } ?>
    </tbody>
  </table>
<?php } ?>

<p class="small">
  More about working here is on the <a href="/careers.php">careers page</a>.
</p>
