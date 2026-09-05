<?php if (trim($q) === '') { ?>
  <p>Type something in the box at the top of the page.</p>
<?php } else { ?>
  <!-- the heading shows the term in the customer's own capitalisation -->
  <h2>Results for <?= bt_out('search.results.markup_escape', 'q', $q) ?></h2>
  <p class="small"><?= count($results) ?> line(s) matched.</p>
  <?php if ($results === []) { ?>
    <p>Nothing matched. Try a shorter word, or the reference from the printed catalogue.</p>
  <?php } else { ?>
    <table class="grid">
      <thead><tr><th>Reference</th><th>Description</th><th class="num">Price</th><th>Unit</th></tr></thead>
      <tbody>
      <?php foreach ($results as $row) { ?>
        <tr>
          <td><a href="/product.php?ref=<?= bt_e(rawurlencode((string) $row['reference'])) ?>"><?= bt_e((string) $row['reference']) ?></a></td>
          <td><?= bt_e((string) $row['name']) ?></td>
          <td class="num"><?= bt_e(bt_money((int) $row['price_pence'])) ?></td>
          <td><?= bt_e((string) $row['unit']) ?></td>
        </tr>
      <?php } ?>
      </tbody>
    </table>
  <?php } ?>
<?php } ?>
