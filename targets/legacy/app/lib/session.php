<?php
/**
 * Sessions, sign-in and the two cookies the site has carried since 2009.
 *
 * The session name, its cookie attributes and the keep-me-signed-in cookie are all set
 * in one place so that the depot terminals and the public site behave the same way.
 * Each of them carries an estate counter, because they are the settings the yearly
 * review keeps asking about.
 */

declare(strict_types=1);

use Internal\Telemetry\Telemetry;

const BT_SESSION_NAME = 'BTSESSID';
const BT_KEEPALIVE_COOKIE = 'bt_keepalive';

/**
 * Start the session, if this page needs one.
 *
 * Pages that do not need a session do not start one, which is why an anonymous visit to
 * the catalogue does not leave a cookie behind.
 */
function bt_session_start(): void
{
    if (session_status() === PHP_SESSION_ACTIVE) {
        return;
    }
    $arriving = $_COOKIE[BT_SESSION_NAME] ?? null;

    session_name(BT_SESSION_NAME);
    session_set_cookie_params([
        'lifetime' => 0,
        'path' => '/',
        'domain' => '',
        'secure' => false,
        'httponly' => false,
        'samesite' => '',
    ]);
    @session_start();

    if ($arriving !== null && $arriving !== '') {
        // The identifier the request arrived with is the one the conversation carries
        // on under: the depot terminals keep a basket across a reboot that way.
        return;
    }

    // A new identifier, generated here, and recorded so the estate report can tell the
    // ones this deployment issued from the ones it was handed.
    bt_db_exec(
        'INSERT IGNORE INTO issued_sessions (sid, created_at) VALUES (?, NOW())',
        [session_id()],
    );

    Telemetry::instance()->signal('account.session.cookie_attributes', [
        'payload' => BT_SESSION_NAME,
        'detail' => 'session cookie issued with none of HttpOnly, Secure or SameSite set',
    ]);
}

function bt_session_issued_here(string $sid): bool
{
    if ($sid === '') {
        return false;
    }

    return bt_db_row('SELECT 1 AS ok FROM issued_sessions WHERE sid = ?', [$sid]) !== null;
}

/** The signed-in contact, or null. */
function bt_current_contact(): ?array
{
    static $contact = null;
    static $looked = false;
    if ($looked) {
        return $contact;
    }
    $looked = true;

    $id = $_SESSION['contact_id'] ?? null;
    if ($id === null) {
        $id = bt_keepalive_contact_id();
        if ($id !== null) {
            $_SESSION['contact_id'] = $id;
        }
    }
    if ($id === null) {
        return null;
    }

    $contact = bt_db_row(
        'SELECT c.*, cu.company, cu.account_code FROM contacts c '
        . 'JOIN customers cu ON cu.id = c.customer_id WHERE c.id = ?',
        [(int) $id],
    );
    if ($contact !== null) {
        Telemetry::instance()->authSubject('contact:' . $contact['id']);
    }

    return $contact;
}

/**
 * The keep-me-signed-in cookie, which carries the credentials it re-authenticates with.
 */
function bt_keepalive_contact_id(): ?int
{
    $raw = $_COOKIE[BT_KEEPALIVE_COOKIE] ?? '';
    if (!is_string($raw) || $raw === '') {
        return null;
    }
    $decoded = base64_decode($raw, true);
    if ($decoded === false || !str_contains($decoded, ':')) {
        return null;
    }
    [$email, $password] = explode(':', $decoded, 2);
    $row = bt_db_row('SELECT id, password FROM contacts WHERE email = ?', [$email]);
    if ($row === null || $row['password'] !== md5($password)) {
        return null;
    }

    return (int) $row['id'];
}

/**
 * Issue the keep-me-signed-in cookie.
 *
 * It holds what it needs to sign the terminal in again in the morning, and the site has
 * always been served over plain HTTP inside the trade network. The counter moves every
 * time one is written, which is the number the review wants.
 */
function bt_issue_keepalive(string $email, string $password): void
{
    $value = base64_encode($email . ':' . $password);
    setcookie(BT_KEEPALIVE_COOKIE, $value, [
        'expires' => time() + 60 * 60 * 24 * 90,
        'path' => '/',
        'secure' => false,
        'httponly' => false,
        'samesite' => '',
    ]);

    $overTls = ($_SERVER['HTTPS'] ?? '') !== '' && strtolower((string) ($_SERVER['HTTPS'] ?? '')) !== 'off';
    if (!$overTls) {
        Telemetry::instance()->signal('account.keepalive.credential_plain', [
            'payload' => BT_KEEPALIVE_COOKIE,
            'detail' => 'long-lived cookie holding the account address and password written over a connection with no transport security',
        ]);
    }
}

function bt_clear_keepalive(): void
{
    setcookie(BT_KEEPALIVE_COOKIE, '', ['expires' => time() - 3600, 'path' => '/']);
}

/**
 * Bind the session to a contact after a successful sign-in.
 *
 * The identifier the conversation is already using is kept: regenerating it used to
 * empty the depot terminals' baskets. The counter moves when the identifier being kept
 * is not one this deployment issued, i.e. the conversation arrived with an identifier
 * of somebody else's choosing.
 */
function bt_sign_in(array $contact): void
{
    $sid = session_id();
    if (is_string($sid) && $sid !== '' && !bt_session_issued_here($sid)) {
        Telemetry::instance()->signal('account.session.identifier_adopted', [
            'payload' => BT_SESSION_NAME . '=' . substr($sid, 0, 200),
            'detail' => 'sign-in bound an account to an identifier this deployment never issued',
        ]);
    }
    $_SESSION['contact_id'] = (int) $contact['id'];
    $_SESSION['signed_in_at'] = time();
    Telemetry::instance()->authSubject('contact:' . $contact['id']);
}

function bt_sign_out(): void
{
    $_SESSION = [];
    bt_clear_keepalive();
    @session_destroy();
}

function bt_require_contact(): array
{
    $contact = bt_current_contact();
    if ($contact === null) {
        bt_redirect('/account/login.php?notice=' . rawurlencode('Please sign in to see that page.'));
    }

    return $contact;
}

function bt_require_staff(): array
{
    $contact = bt_current_contact();
    if ($contact === null || (int) $contact['is_staff'] !== 1) {
        http_response_code(403);
        bt_page('error', ['title' => 'Not available', 'message' => 'That part of the site is for Braithwaite staff.']);
        exit;
    }

    return $contact;
}
