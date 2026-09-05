<?php if ($sent) { ?>
  <p class="notice">Thank you. The trade desk has your details and will ring you at the time you asked for.</p>
<?php } ?>

<p>
  If it is easier for us to ring you, leave your number and say when suits. Weekday
  mornings are the busiest hour at the counters, so between ten and half past three we
  normally get back the same day.
</p>

<form method="post" action="/callback.php" class="stacked">
  <p><label for="name">Your name</label><input type="text" id="name" name="name" size="34"></p>
  <p><label for="company">Company</label><input type="text" id="company" name="company" size="34"></p>
  <p><label for="email">E-mail</label><input type="text" id="email" name="email" size="34"></p>
  <p><label for="phone">Telephone</label><input type="text" id="phone" name="phone" size="20"></p>
  <p><label for="when">Best time to ring</label><input type="text" id="when" name="when" size="30"></p>
  <p><button type="submit">Ask for a call back</button></p>
</form>

<p class="small">
  In a hurry? The trade desk is on 01422 000000 from seven in the morning.
</p>
