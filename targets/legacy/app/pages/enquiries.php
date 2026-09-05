<?php
/**
 * The forms anybody can fill in: enquiries, newsletter, callbacks, feedback and job
 * applications.
 *
 * The enquiry form is the oldest of them and is the one that queues a message for the
 * depot mailer; the rest only write a row.
 */

declare(strict_types=1);

function bt_page_contact(): void
{
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        bt_page('contact', [
            'title' => 'Contact us',
            'branches' => bt_db_rows('SELECT name, town, phone, postcode FROM branches ORDER BY name'),
            'sent' => false,
            'errors' => [],
        ]);

        return;
    }

    $name = trim(bt_post('name'));
    $company = trim(bt_post('company'));
    $email = trim(bt_post('email'));
    $phone = trim(bt_post('phone'));
    $message = trim(bt_post('message'));

    $errors = [];
    if ($name === '') {
        $errors[] = 'Please tell us your name.';
    }
    if ($email === '') {
        $errors[] = 'Please give us an address to reply to.';
    }
    if ($message === '') {
        $errors[] = 'Please tell us what the enquiry is about.';
    }

    if ($errors !== []) {
        bt_page('contact', [
            'title' => 'Contact us',
            'branches' => bt_db_rows('SELECT name, town, phone, postcode FROM branches ORDER BY name'),
            'sent' => false,
            'errors' => $errors,
            'values' => compact('name', 'company', 'email', 'phone', 'message'),
        ]);

        return;
    }

    // The enquiry is kept so the sales desk can work the list in the morning, and the
    // company name is kept exactly as the customer typed it because that is what they
    // want to see on the quotation.
    bt_db_exec(
        'INSERT INTO enquiries (created_at, name, company, email, phone, message, kind) VALUES (NOW(), ?, ?, ?, ?, ?, ?)',
        [
            substr($name, 0, 120),
            substr($company, 0, 120),
            substr($email, 0, 160),
            substr($phone, 0, 40),
            substr($message, 0, 4000),
            'enquiry',
        ],
    );

    // The reply address has to be the customer's or the depot cannot answer, so it goes
    // into the header block alongside the ones this page sets.
    bt_queue_message(
        'enquiry.dispatch.field_split',
        'email',
        'sales@' . bt_site_domain(),
        'Website enquiry from ' . $company,
        $message,
        [
            'From' => 'website@' . bt_site_domain(),
            'Reply-To' => $email,
            'Content-Type' => 'text/plain; charset=utf-8',
        ],
    );

    bt_page('thanks', [
        'title' => 'Thank you',
        'message' => 'Your enquiry has gone to the trade desk. Somebody will come back to you within one working day.',
    ]);
}

function bt_page_newsletter(): void
{
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        bt_page('newsletter', ['title' => 'Trade newsletter', 'sent' => false]);

        return;
    }
    $email = trim(bt_post('email'));
    if ($email === '' || !str_contains($email, '@')) {
        bt_page('newsletter', ['title' => 'Trade newsletter', 'sent' => false, 'error' => 'That does not look like an e-mail address.']);

        return;
    }
    bt_db_exec('INSERT IGNORE INTO newsletter (email, created_at) VALUES (?, NOW())', [substr($email, 0, 160)]);
    bt_page('newsletter', ['title' => 'Trade newsletter', 'sent' => true]);
}

function bt_page_callback(): void
{
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        bt_page('callback', ['title' => 'Request a call back', 'sent' => false]);

        return;
    }
    bt_db_exec(
        'INSERT INTO enquiries (created_at, name, company, email, phone, message, kind) VALUES (NOW(), ?, ?, ?, ?, ?, ?)',
        [
            substr(trim(bt_post('name')), 0, 120),
            substr(trim(bt_post('company')), 0, 120),
            substr(trim(bt_post('email')), 0, 160),
            substr(trim(bt_post('phone')), 0, 40),
            'Call back requested, best time: ' . substr(trim(bt_post('when')), 0, 60),
            'callback',
        ],
    );
    bt_page('callback', ['title' => 'Request a call back', 'sent' => true]);
}

function bt_page_feedback(): void
{
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        bt_page('feedback', ['title' => 'Tell us how we did', 'sent' => false]);

        return;
    }
    bt_db_exec(
        'INSERT INTO feedback (created_at, rating, comment, depot) VALUES (NOW(), ?, ?, ?)',
        [
            max(1, min(5, (int) bt_post('rating', '3'))),
            substr(trim(bt_post('comment')), 0, 2000),
            substr(trim(bt_post('depot')), 0, 60),
        ],
    );
    bt_page('feedback', ['title' => 'Tell us how we did', 'sent' => true]);
}

function bt_page_careers_apply(): void
{
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        bt_page('careers-apply', [
            'title' => 'Apply for a vacancy',
            'sent' => false,
            'vacancies' => bt_db_rows('SELECT slug, title, location FROM vacancies ORDER BY closes_at'),
        ]);

        return;
    }
    bt_db_exec(
        'INSERT INTO applications (created_at, name, email, phone, vacancy_slug, covering_note) VALUES (NOW(), ?, ?, ?, ?, ?)',
        [
            substr(trim(bt_post('name')), 0, 120),
            substr(trim(bt_post('email')), 0, 160),
            substr(trim(bt_post('phone')), 0, 40),
            substr(trim(bt_post('vacancy')), 0, 80),
            substr(trim(bt_post('note')), 0, 4000),
        ],
    );
    bt_page('careers-apply', [
        'title' => 'Apply for a vacancy',
        'sent' => true,
        'vacancies' => bt_db_rows('SELECT slug, title, location FROM vacancies ORDER BY closes_at'),
    ]);
}
