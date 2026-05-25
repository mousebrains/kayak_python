<?php

declare(strict_types=1);

/**
 * Editor auth helpers — sessions + CSRF + feature flag.
 *
 * Cookies:
 *   ed_sess  Random 32-byte hex value; only sha256(value) stored server-side.
 *            7-day flat expiry. HttpOnly, Secure on HTTPS, SameSite=Strict, Path=/.
 *   ed_csrf  Random 32-byte hex value. Double-submit CSRF token. Same flags.
 *
 * Maintainer sessions reuse the same ed_sess cookie; role is determined by
 * the editor.status column, so one cookie type covers both user classes.
 *
 * Magic-link flow (issue_/peek_/consume_magic_link, normalize_email,
 * safe_next_url) lives in auth_magic_link.php — included transitively
 * at the bottom of this file, so consumers don't need a second
 * require_once. Split out as part of Tier 5.A so the auth-core stays
 * focused on session+CSRF.
 */

require_once __DIR__ . '/db.php';
require_once __DIR__ . '/config.php';

// ---------------------------------------------------------------------------
// Feature flag
// ---------------------------------------------------------------------------

function editor_feature_enabled(): bool
{
    return Config::bool('editor_feature', false);
}

/** Abort with 404 if the feature flag is not on. */
function require_editor_feature(): void
{
    if (!editor_feature_enabled()) {
        require_once __DIR__ . '/error.php';
        render_error_page(404, 'Not found', '<p>This page is not available.</p>');
    }
}

// ---------------------------------------------------------------------------
// Token utilities
// ---------------------------------------------------------------------------

/** @param int<1, max> $bytes */
function generate_token(int $bytes = 32): string
{
    return bin2hex(random_bytes($bytes));
}

function hash_token(string $tok): string
{
    return hash('sha256', $tok);
}

// ---------------------------------------------------------------------------
// Session cookie
// ---------------------------------------------------------------------------

const EDITOR_SESSION_COOKIE = 'ed_sess';
const EDITOR_SESSION_DAYS   = 7;
const EDITOR_CSRF_COOKIE    = 'ed_csrf';

/**
 * @return array{expires:int, path:string, secure:bool, httponly:bool, samesite:'Strict'}
 */
function _cookie_params(int $lifetime_seconds): array
{
    return [
        'expires'  => $lifetime_seconds === 0 ? 0 : time() + $lifetime_seconds,
        'path'     => '/',
        'secure'   => ($_SERVER['HTTPS'] ?? '') !== '',
        'httponly' => true,
        'samesite' => 'Strict',
    ];
}

/**
 * Create a session row for the given editor, set the cookie, return the
 * cookie value (hex token).
 */
function set_editor_session(int $editor_id): string
{
    $tok = generate_token();
    $hash = hash_token($tok);
    $expires = gmdate('Y-m-d H:i:s', time() + EDITOR_SESSION_DAYS * 86400);
    $ip = $_SERVER['REMOTE_ADDR'] ?? '';
    $ua = substr((string)($_SERVER['HTTP_USER_AGENT'] ?? ''), 0, 512);

    $db = get_db();
    $stmt = $db->prepare(
        'INSERT INTO editor_session
           (editor_id, token_hash, created_at, expires_at, last_seen_at, ip, user_agent)
         VALUES (?, ?, datetime(\'now\'), ?, datetime(\'now\'), ?, ?)'
    );
    $stmt->execute([$editor_id, $hash, $expires, $ip, $ua]);

    $db->prepare('UPDATE editor SET last_login_at = datetime(\'now\') WHERE id = ?')
        ->execute([$editor_id]);

    setcookie(EDITOR_SESSION_COOKIE, $tok, _cookie_params(EDITOR_SESSION_DAYS * 86400));
    $_COOKIE[EDITOR_SESSION_COOKIE] = $tok;

    // Rotate the CSRF token on privilege escalation: pre-auth cookie never
    // carries into the post-auth session, closing the classic fixation vector.
    $new_csrf = generate_token();
    setcookie(EDITOR_CSRF_COOKIE, $new_csrf, _cookie_params(0));
    $_COOKIE[EDITOR_CSRF_COOKIE] = $new_csrf;

    return $tok;
}

/** Revoke the current session (if any) and clear the cookie.
 *
 * $db_override is for tests only; production calls pass nothing and use
 * the global get_db() connection.
 */
function clear_editor_session(?PDO $db_override = null): void
{
    $tok = $_COOKIE[EDITOR_SESSION_COOKIE] ?? '';
    if ($tok !== '') {
        $hash = hash_token($tok);
        ($db_override ?? get_db())->prepare(
            'UPDATE editor_session SET revoked_at = datetime(\'now\') WHERE token_hash = ?'
        )->execute([$hash]);
    }
    setcookie(EDITOR_SESSION_COOKIE, '', _cookie_params(-3600));
    unset($_COOKIE[EDITOR_SESSION_COOKIE]);
}

/**
 * Return the editor row (+ session id) for the current request, or null.
 *
 * Does NOT redirect; use require_editor() / require_maintainer() for that.
 *
 * $db_override is for tests only; when present the per-request memoization
 * is bypassed so different sessions can be exercised within the same PHP
 * process. Production callers pass nothing.
 *
 * @return array<string, mixed>|null  Editor + session-join row, or null when no valid session.
 */
function current_editor(?PDO $db_override = null): ?array
{
    static $cached = false;
    static $editor = null;
    if ($db_override === null && $cached) {
        return $editor;
    }

    $tok = (string)($_COOKIE[EDITOR_SESSION_COOKIE] ?? '');
    if ($tok === '' || !ctype_xdigit($tok) || strlen($tok) !== 64) {
        if ($db_override === null) {
            $cached = true;
            $editor = null;
        }
        return null;
    }

    $hash = hash_token($tok);
    $stmt = ($db_override ?? get_db())->prepare(
        'SELECT e.*, s.id AS session_id, s.expires_at AS session_expires_at,
                s.last_seen_at AS session_last_seen_at
         FROM editor_session s
         JOIN editor e ON e.id = s.editor_id
         WHERE s.token_hash = ?
           AND s.revoked_at IS NULL
           AND s.expires_at > datetime(\'now\')
           AND e.status != ?'
    );
    $stmt->execute([$hash, 'banned']);
    $row = $stmt->fetch();
    if (!$row) {
        if ($db_override === null) {
            $cached = true;
            $editor = null;
        }
        return null;
    }

    // Throttle to ~60s and swallow SQLITE_BUSY. This runs on every page
    // load via render_nav(); without the throttle, service-worker prefetch
    // bursts plus a concurrent pipeline write can exhaust busy_timeout and
    // turn a bookkeeping update into a 500.
    $last_ts = (int)strtotime((string)($row['session_last_seen_at'] ?? '') . ' UTC');
    if ($last_ts < time() - 60) {
        try {
            ($db_override ?? get_db())->prepare('UPDATE editor_session SET last_seen_at = datetime(\'now\') WHERE id = ?')
                ->execute([$row['session_id']]);
        } catch (\PDOException $e) {
            error_log('editor_session last_seen_at update skipped: ' . $e->getMessage());
        }
    }
    if ($db_override === null) {
        $cached = true;
        $editor = $row;
    }
    return $row;
}

/** @param array<string, mixed>|null $ed */
function is_maintainer(?array $ed = null): bool
{
    $ed ??= current_editor();
    return $ed !== null && ($ed['status'] ?? '') === 'maintainer';
}

/** @return array<string, mixed> */
function require_editor(): array
{
    $ed = current_editor();
    if ($ed === null) {
        $next = rawurlencode($_SERVER['REQUEST_URI'] ?? '/');
        header("Location: /login.php?next=$next");
        exit;
    }
    return $ed;
}

/** @return array<string, mixed> */
function require_maintainer(): array
{
    $ed = require_editor();
    if (!is_maintainer($ed)) {
        require_once __DIR__ . '/error.php';
        render_error_page(
            403,
            'Not allowed',
            '<p>This page is only available to the site maintainer. You are signed in as '
            . htmlspecialchars((string)$ed['email'])
            . ' (' . htmlspecialchars((string)$ed['status']) . ').</p>'
            . '<p>If you think this is a mistake, please <a href="/contact.php">contact the maintainer</a>.</p>'
        );
    }
    return $ed;
}

// ---------------------------------------------------------------------------
// CSRF — double-submit cookie pattern
// ---------------------------------------------------------------------------

function csrf_token(): string
{
    $tok = (string)($_COOKIE[EDITOR_CSRF_COOKIE] ?? '');
    if ($tok === '' || !ctype_xdigit($tok) || strlen($tok) !== 64) {
        $tok = generate_token();
        setcookie(EDITOR_CSRF_COOKIE, $tok, _cookie_params(0));
        $_COOKIE[EDITOR_CSRF_COOKIE] = $tok;
    }
    return $tok;
}

function require_csrf(): void
{
    $submitted = (string)($_POST['csrf_token'] ?? '');
    $cookie    = (string)($_COOKIE[EDITOR_CSRF_COOKIE] ?? '');
    if ($submitted === '' || $cookie === '' || !hash_equals($cookie, $submitted)) {
        http_response_code(403);
        exit('Invalid CSRF token');
    }
}

// ---------------------------------------------------------------------------
// Maintainer notification routing
// ---------------------------------------------------------------------------

/**
 * Return the email address(es) of the site maintainer(s) for notifications.
 * Priority: MAINTAINER_EMAIL env var, then all editor rows with
 * status='maintainer', then a hard-coded fallback.
 *
 * @return list<string>
 */
function maintainer_emails(): array
{
    // 1. ``MAINTAINER_EMAIL`` env (singular, CSV) lets the operator
    //    override the JSON snapshot without re-running emit-config.
    $env = getenv('MAINTAINER_EMAIL');
    if ($env !== false && $env !== '') {
        return array_values(array_filter(array_map('trim', explode(',', $env)), fn($s) => $s !== ''));
    }
    // 2. ``maintainer_emails`` from /etc/kayak/runtime-config.json
    //    (list[EmailStr] from KayakConfig).
    $cfg = Config::list('maintainer_emails');
    if ($cfg !== []) {
        return $cfg;
    }
    // 3. DB-rows fallback — the documented "no env / JSON" behavior.
    try {
        $stmt = get_db()->prepare(
            "SELECT email FROM editor WHERE status = 'maintainer' ORDER BY id"
        );
        $stmt->execute();
        $rows = array_column($stmt->fetchAll(), 'email');
        if ($rows) {
            return $rows;
        }
    } catch (Throwable) {
        // fall through
    }
    // 4. Empty + WARN. The hardcoded ``pat.kayak@gmail.com`` literal
    //    that used to live here is gone — the JSON guarantees at least
    //    an empty list, so this branch only fires when nothing is set.
    error_log('[CONFIG-FALLBACK] maintainer_emails: no env / JSON / DB-row source available');
    return [];
}

// ---------------------------------------------------------------------------
// Magic-link flow — split out into auth_magic_link.php (Tier 5.A.1).
// Required at the bottom so the magic-link file can `require auth.php` for
// generate_token + hash_token without a load-order cycle.
// ---------------------------------------------------------------------------

require_once __DIR__ . '/auth_magic_link.php';
