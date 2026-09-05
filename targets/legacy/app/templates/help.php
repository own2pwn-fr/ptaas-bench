<div class="two-column">
  <div class="side">
    <h2>Help topics</h2>
    <ul class="plain">
      <?php foreach ($articles as $article) { ?>
        <li><a href="/help.php?topic=<?= bt_e(rawurlencode($article[0])) ?>"><?= bt_e($article[1]) ?></a></li>
      <?php } ?>
    </ul>
    <p class="small">If the answer is not here, the trade desk is on 01422 000000 from 7am.</p>
  </div>
  <div class="main">
    <?php
      // Articles are partial templates on disk; the link carries the file name.
      if (!bt_include_from('help.article.include_scope', 'topic', BT_HELP, $topic)) {
          echo '<p>That help page is not here any more. Pick one from the list.</p>';
      }
    ?>
  </div>
</div>
