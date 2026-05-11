<?php
declare(strict_types=1);
/**
 * Editor auth helpers — magic-link sessions + CSRF.
 *
 * Cookies:
 *   ed_sess  Random 32-byte hex value; only sha256(value) stored server-side.
 *            7-day flat expiry. HttpOnly, Secure on HTTPS, SameSite=Strict, Path=/.
 *   ed_csrf  Random 32-byte hex value. Double-submit CSRF token. Same flags.
 *
 * Maintainer sessions reuse the same ed_sess cookie; role is determined by
 * the editor.status column, so one cookie type covers both user classes.
 */

require_once __DIR__ . '/db.php';

// ---------------------------------------------------------------------------
// Feature flag
// ---------------------------------------------------------------------------

function auth_env(string $name): string {
    $v = getenv($name);
    if ($v === false || $v === '') $v = (string)($_SERVER[$name] ?? '');
    return $v;
}

function editor_feature_enabled(): bool {
    $v = auth_env('EDITOR_FEATURE');
    return $v === '1' || strcasecmp($v, 'true') === 0;
}

/** Abort with 404 if the feature flag is not on. */
function require_editor_feature(): void {
    if (!editor_feature_enabled()) {
        require_once __DIR__ . '/error.php';
        render_error_page(404, 'Not found', '<p>This page is not available.</p>');
    }
}

// ---------------------------------------------------------------------------
// Token utilities
// ---------------------------------------------------------------------------

function generate_token(int $bytes = 32): string {
    return bin2hex(random_bytes($bytes));
}

function hash_token(string $tok): string {
    return hash('sha256', $tok);
}

// ---------------------------------------------------------------------------
// Session cookie
// ---------------------------------------------------------------------------

const EDITOR_SESSION_COOKIE = 'ed_sess';
const EDITOR_SESSION_DAYS   = 7;
const EDITOR_CSRF_COOKIE    = 'ed_csrf';

function _cookie_params(int $lifetime_seconds): array {
    return [
        'expires'  => $lifetime_seconds === 0 ? 0 : time() + $lifetime_seconds,
        'path'     => '/',
        'secure'   => !empty($_SERVER['HTTPS']),
        'httponly' => true,
        'samesite' => 'Strict',
    ];
}

/**
 * Create a session row for the given editor, set the cookie, return the
 * cookie value (hex token).
 */
function set_editor_session(int $editor_id): string {
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

/** Revoke the current session (if any) and clear the cookie. */
function clear_editor_session(): void {
    $tok = $_COOKIE[EDITOR_SESSION_COOKIE] ?? '';
    if ($tok !== '') {
        $hash = hash_token($tok);
        get_db()->prepare(
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
 */
function current_editor(): ?array {
    static $cached = false;
    static $editor = null;
    if ($cached) return $editor;
    $cached = true;

    $tok = $_COOKIE[EDITOR_SESSION_COOKIE] ?? '';
    if ($tok === '' || !ctype_xdigit($tok) || strlen($tok) !== 64) return $editor = null;

    $hash = hash_token($tok);
    $stmt = get_db()->prepare(
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
    if (!$row) return $editor = null;

    // Throttle to ~60s and swallow SQLITE_BUSY. This runs on every page
    // load via render_nav(); without the throttle, service-worker prefetch
    // bursts plus a concurrent pipeline write can exhaust busy_timeout and
    // turn a bookkeeping update into a 500.
    $last_ts = (int)strtotime((string)($row['session_last_seen_at'] ?? '') . ' UTC');
    if ($last_ts < time() - 60) {
        try {
            get_db()->prepare('UPDATE editor_session SET last_seen_at = datetime(\'now\') WHERE id = ?')
                ->execute([$row['session_id']]);
        } catch (\PDOException $e) {
            error_log('editor_session last_seen_at update skipped: ' . $e->getMessage());
        }
    }
    return $editor = $row;
}

function is_maintainer(?array $ed = null): bool {
    $ed ??= current_editor();
    return $ed !== null && ($ed['status'] ?? '') === 'maintainer';
}

function require_editor(): array {
    $ed = current_editor();
    if ($ed === null) {
        $next = rawurlencode($_SERVER['REQUEST_URI'] ?? '/');
        header("Location: /login.php?next=$next");
        exit;
    }
    return $ed;
}

function require_maintainer(): array {
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

function csrf_token(): string {
    $tok = $_COOKIE[EDITOR_CSRF_COOKIE] ?? '';
    if ($tok === '' || !ctype_xdigit($tok) || strlen($tok) !== 64) {
        $tok = generate_token();
        setcookie(EDITOR_CSRF_COOKIE, $tok, _cookie_params(0));
        $_COOKIE[EDITOR_CSRF_COOKIE] = $tok;
    }
    return $tok;
}

function require_csrf(): void {
    $submitted = (string)($_POST['csrf_token'] ?? '');
    $cookie    = (string)($_COOKIE[EDITOR_CSRF_COOKIE] ?? '');
    if ($submitted === '' || $cookie === '' || !hash_equals($cookie, $submitted)) {
        http_response_code(403);
        exit('Invalid CSRF token');
    }
}

// ---------------------------------------------------------------------------
// Magic-link flow
// ---------------------------------------------------------------------------

/** Normalize an email address: trim, lowercase. */
function normalize_email(string $email): string {
    return strtolower(trim($email));
}

/**
 * Cap on magic-link issuance per email and per IP within a rolling hour.
 * Returns true when the caller should proceed. Same silent response for
 * "over the cap" and "under the cap" keeps the login.php UX identical so
 * we don't leak whether the email exists.
 */
function magic_link_under_throttle(PDO $db, string $email, string $ip): bool {
    $email_cap = 5;    // magic links per email per hour
    $ip_cap    = 20;   // magic links from one IP per hour (shared households)

    if ($email !== '') {
        $stmt = $db->prepare(
            "SELECT COUNT(*) FROM editor_magic_link eml
             JOIN editor e ON e.id = eml.editor_id
             WHERE e.email = ? AND eml.created_at > datetime('now', '-1 hour')"
        );
        $stmt->execute([$email]);
        if ((int)$stmt->fetchColumn() >= $email_cap) return false;
    }
    if ($ip !== '') {
        $stmt = $db->prepare(
            "SELECT COUNT(*) FROM editor_magic_link
             WHERE ip_issued = ? AND created_at > datetime('now', '-1 hour')"
        );
        $stmt->execute([$ip]);
        if ((int)$stmt->fetchColumn() >= $ip_cap) return false;
    }
    return true;
}

/**
 * Upsert an editor by email and issue a magic-link token. Returns the
 * raw token (to embed in a URL) and the editor id.
 *
 * New editors are created with status='pending'. Does not send email.
 */
function issue_magic_link(string $email, ?string $next_url = null): array {
    $email = normalize_email($email);
    if (!filter_var($email, FILTER_VALIDATE_EMAIL)) {
        throw new RuntimeException('Invalid email');
    }
    $ip = (string)($_SERVER['REMOTE_ADDR'] ?? '');
    $db = get_db();
    // Rate-limit first — cheap read, returns the same outward response as
    // a successful issuance so we don't leak which emails have been tried.
    if (!magic_link_under_throttle($db, $email, $ip)) {
        return ['editor_id' => 0, 'token' => '', 'banned' => true];
    }
    $db->beginTransaction();
    try {
        $stmt = $db->prepare('SELECT id, status FROM editor WHERE email = ?');
        $stmt->execute([$email]);
        $ed = $stmt->fetch();
        if (!$ed) {
            $db->prepare(
                "INSERT INTO editor (email, status, created_at) VALUES (?, 'pending', datetime('now'))"
            )->execute([$email]);
            $editor_id = (int)$db->lastInsertId();
        } else {
            $editor_id = (int)$ed['id'];
            if ($ed['status'] === 'banned') {
                $db->commit();
                return ['editor_id' => $editor_id, 'token' => '', 'banned' => true];
            }
        }

        $tok = generate_token();
        $hash = hash_token($tok);
        $expires = gmdate('Y-m-d H:i:s', time() + 30 * 60);

        $db->prepare(
            "INSERT INTO editor_magic_link
               (editor_id, token_hash, created_at, expires_at, ip_issued, next_url)
             VALUES (?, ?, datetime('now'), ?, ?, ?)"
        )->execute([$editor_id, $hash, $expires, $ip, $next_url]);

        $db->commit();
        return ['editor_id' => $editor_id, 'token' => $tok, 'banned' => false];
    } catch (Throwable $e) {
        $db->rollBack();
        throw $e;
    }
}

/**
 * Consume a magic-link token. On success returns [editor_id, next_url]
 * and marks the token used. On failure returns null.
 */
/**
 * Check whether a magic-link token is currently valid (exists, unused,
 * unexpired) WITHOUT consuming it. Used by auth.php's GET handler to
 * decide between rendering the interstitial form vs. the expired page.
 * Email-scanner URL prefetch (Outlook Defender, Proofpoint, etc.) only
 * hits GET, so this read is a no-op for them — the token stays unused
 * until the actual user POSTs the form.
 */
function peek_magic_link(string $tok): bool {
    if ($tok === '' || !ctype_xdigit($tok) || strlen($tok) !== 64) return false;
    $hash = hash_token($tok);
    $db = get_db();
    $stmt = $db->prepare(
        "SELECT 1 FROM editor_magic_link
         WHERE token_hash = ? AND used_at IS NULL AND expires_at > datetime('now')"
    );
    $stmt->execute([$hash]);
    return $stmt->fetchColumn() !== false;
}

function consume_magic_link(string $tok): ?array {
    if ($tok === '' || !ctype_xdigit($tok) || strlen($tok) !== 64) return null;
    $hash = hash_token($tok);
    $db = get_db();
    $db->beginTransaction();
    try {
        $stmt = $db->prepare(
            "SELECT id, editor_id, next_url FROM editor_magic_link
             WHERE token_hash = ? AND used_at IS NULL AND expires_at > datetime('now')"
        );
        $stmt->execute([$hash]);
        $row = $stmt->fetch();
        if (!$row) { $db->commit(); return null; }

        $db->prepare(
            "UPDATE editor_magic_link SET used_at = datetime('now') WHERE id = ?"
        )->execute([$row['id']]);
        $db->commit();
        return ['editor_id' => (int)$row['editor_id'], 'next_url' => $row['next_url']];
    } catch (Throwable $e) {
        $db->rollBack();
        return null;
    }
}

/**
 * Validate a post-login redirect target. Only allow same-origin paths.
 *
 * Rejects:
 *   - protocol-relative `//host` (browsers send the user off-site)
 *   - `/\host` — per the WHATWG URL spec, browsers normalize `\` to `/`
 *     in special-scheme URLs, so a leading `/\` becomes `//`.
 */
function safe_next_url(?string $next): string {
    if ($next === null || $next === '') return '/';
    if (!preg_match('#^/[^/\\\\]#', $next)) return '/';
    return $next;
}

/**
 * Return the email address(es) of the site maintainer(s) for notifications.
 * Priority: MAINTAINER_EMAIL env var, then all editor rows with
 * status='maintainer', then a hard-coded fallback.
 */
function maintainer_emails(): array {
    $env = auth_env('MAINTAINER_EMAIL');
    if ($env !== '') {
        return array_values(array_filter(array_map('trim', explode(',', $env))));
    }
    try {
        $stmt = get_db()->prepare(
            "SELECT email FROM editor WHERE status = 'maintainer' ORDER BY id"
        );
        $stmt->execute();
        $rows = array_column($stmt->fetchAll(), 'email');
        if ($rows) return $rows;
    } catch (Throwable) {
        // fall through
    }
    return ['pat.kayak@gmail.com'];
}
