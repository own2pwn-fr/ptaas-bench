<p>
  Tell us what you need and the trade desk will price it, usually the same day. If the
  order is over five hundred pounds you can spread it with our finance partner; the
  monthly figure is worked out in the panel below.
</p>

<form method="post" action="/quote-request.php" class="stacked">
  <p><label for="name">Your name</label><input type="text" id="name" name="name" size="34"></p>
  <p><label for="company">Company</label><input type="text" id="company" name="company" size="34"></p>
  <p><label for="email">E-mail</label><input type="text" id="email" name="email" size="34"></p>
  <p><label for="phone">Telephone</label><input type="text" id="phone" name="phone" size="20"></p>
  <p><label for="reference">Your reference</label><input type="text" id="reference" name="reference" size="20"></p>
  <p>
    <label for="branch">Collect from</label>
    <select id="branch" name="branch">
      <?php foreach ($branches as $branch) { ?>
        <option value="<?= bt_e((string) $branch['name']) ?>"><?= bt_e((string) $branch['name']) ?></option>
      <?php } ?>
    </select>
  </p>
  <p><label for="message">What do you need?</label><textarea id="message" name="message" rows="6" cols="50"></textarea></p>
  <p><button type="submit">Send it to the trade desk</button></p>
</form>

<div id="affordability" class="panel">
  <p class="small">Monthly figures provided by our finance partner.</p>
</div>
<!-- affordability panel, snippet as published by the partner -->
<script src="<?= bt_e($widget) ?>" data-account="BT-TRADE" async></script>
