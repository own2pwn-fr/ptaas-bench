<?php if ($sent) { ?>
  <p class="notice">Thank you. Your application is with the credit control office.</p>
  <p>
    One of the credit control team at Elland will be in touch within two working days,
    normally by telephone, to confirm the trade references and settle the credit limit.
    Once the account is opened we will post the account code out and you can sign in here
    to see orders, statements and delivery paperwork.
  </p>
  <ul class="plain">
    <li><a href="/account/login.php">Sign in</a></li>
    <li><a href="/credit-account.php">How our 30-day credit accounts work</a></li>
    <li><a href="/branches.php">Our eight depots</a></li>
  </ul>
<?php } else { ?>
  <p>
    A trade account gives you depot prices, 30-day credit terms, next-day delivery on
    stock lines and the paperwork kept in one place. Fill this in and the credit control
    office at Elland will take it from there.
  </p>

  <form method="post" action="/account/register.php" class="stacked">
    <p>
      <label for="name">Your name</label>
      <input type="text" id="name" name="name" size="34">
    </p>
    <p>
      <label for="company">Company</label>
      <input type="text" id="company" name="company" size="34">
    </p>
    <p>
      <label for="email">E-mail address</label>
      <input type="text" id="email" name="email" size="34">
    </p>
    <p>
      <label for="phone">Telephone</label>
      <input type="text" id="phone" name="phone" size="20">
    </p>
    <p>
      <label for="references">Trade references</label>
      <textarea id="references" name="references" rows="6" cols="50"></textarea>
    </p>
    <p class="small">
      Two trade references please, with a contact name and telephone number for each, plus
      your company registration number if you have one.
    </p>
    <p><button type="submit">Send the application</button></p>
  </form>
<?php } ?>

<h2>Elsewhere in your account</h2>
<ul class="inline small">
  <li><a href="/account/index.php">Summary</a></li>
  <li><a href="/account/orders.php">Orders and quotations</a></li>
  <li><a href="/account/statements.php">Statements</a></li>
  <li><a href="/account/documents.php">Delivery paperwork</a></li>
  <li><a href="/account/deliveries.php">Deliveries due</a></li>
  <li><a href="/account/favourites.php">Saved lines</a></li>
  <li><a href="/account/templates.php">Order templates</a></li>
  <li><a href="/account/addresses.php">Delivery addresses</a></li>
  <li><a href="/account/contacts.php">People on the account</a></li>
  <li><a href="/account/users.php">Sign-in access</a></li>
  <li><a href="/account/quote.php">Request a quotation</a></li>
  <li><a href="/account/profile.php">Your details</a></li>
  <li><a href="/account/preferences.php">Preferences</a></li>
  <li><a href="/account/password.php">Change your password</a></li>
  <li><a href="/account/logout.php">Sign out</a></li>
</ul>
