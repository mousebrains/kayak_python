<?php

declare(strict_types=1);

/**
 * Magic-link issuance + consumption — the email-based login flow.
 *
 * Split out of auth.php as part of Tier 5.A. Three consumers
 * (login.php, /src/kayak/web/php/auth.php, includes/auth.php transitive) so the
 * cluster boundary is clean: nothing outside the login flow needs
 * to see these helpers.
 *
 * Token lifecycle:
 *   1. issue_magic_link($email)        → write editor_magic_link row, return raw token + editor_id
 *   2. peek_magic_link($tok)           → bool — used by /auth.php's GET to gate the interstitial
 *   3. consume_magic_link($tok)        → marks the row used, returns [editor_id, next_url]
 *
 * Throttle: magic_link_under_throttle() caps issuance at 5/email/hr
 * and 20/IP/hr. Same outward response shape for over/under cap so
 * /login.php doesn't leak whether the email exists.
 *
 * Public exports (no leading underscore — used outside this file):
 *   normalize_email, magic_link_under_throttle, issue_magic_link,
 *   peek_magic_link, consume_magic_link, safe_next_url
 */

require_once __DIR__ . '/db.php';
// Back-require for the rare caller that includes auth_magic_link.php
// directly (none today — login.php and /src/kayak/web/php/auth.php go through
// includes/auth.php). PHP's require_once is idempotent, so the
// apparent cycle with auth.php → auth_magic_link.php is safe.
require_once __DIR__ . '/auth.php';

/**
 * Normalize an email address for rate-limiting and lookup.
 *
 * Trim + lowercase universally. For Gmail (gmail.com / googlemail.com)
 * apply Gmail's delivery rules so the same mailbox doesn't appear to
 * be N distinct addresses:
 *   - strip "+tag" from the local part (foo+bar@gmail.com -> foo@gmail.com)
 *   - strip dots in the local part (f.o.o@gmail.com -> foo@gmail.com)
 *   - googlemail.com -> gmail.com (alias domain)
 * This closes the magic-link-per-email rate cap bypass routes via the
 * obvious +tag / dot-alias aliases. Other providers untouched — some
 * treat the local part more literally and our rate cap is best-effort
 * anyway.
 */
function normalize_email(string $email): string
{
    $email = strtolower(trim($email));
    $at = strrpos($email, '@');
    if ($at === false) {
        return $email;
    }
    $local  = substr($email, 0, $at);
    $domain = substr($email, $at + 1);
    if ($domain === 'gmail.com' || $domain === 'googlemail.com') {
        $plus = strpos($local, '+');
        if ($plus !== false) {
            $local = substr($local, 0, $plus);
        }
        $local  = str_replace('.', '', $local);
        $domain = 'gmail.com';
    }
    return $local . '@' . $domain;
}

/**
 * Cap on magic-link issuance per email and per IP within a rolling hour.
 * Returns true when the caller should proceed. Same silent response for
 * "over the cap" and "under the cap" keeps the login.php UX identical so
 * we don't leak whether the email exists.
 */
function magic_link_under_throttle(PDO $db, string $email, string $ip): bool
{
    $email_cap = 5;    // magic links per email per hour
    $ip_cap    = 20;   // magic links from one IP per hour (shared households)

    if ($email !== '') {
        $stmt = $db->prepare(
            "SELECT COUNT(*) FROM editor_magic_link eml
             JOIN editor e ON e.id = eml.editor_id
             WHERE e.email = ? AND eml.created_at > datetime('now', '-1 hour')"
        );
        $stmt->execute([$email]);
        if ((int)$stmt->fetchColumn() >= $email_cap) {
            return false;
        }
    }
    if ($ip !== '') {
        $stmt = $db->prepare(
            "SELECT COUNT(*) FROM editor_magic_link
             WHERE ip_issued = ? AND created_at > datetime('now', '-1 hour')"
        );
        $stmt->execute([$ip]);
        if ((int)$stmt->fetchColumn() >= $ip_cap) {
            return false;
        }
    }
    return true;
}

/**
 * Upsert an editor by email and issue a magic-link token. Returns the
 * raw token (to embed in a URL) and the editor id.
 *
 * New editors are created with status='pending'. Does not send email.
 *
 * @return array{editor_id: int, token: string, banned: bool}
 */
function issue_magic_link(string $email, ?string $next_url = null): array
{
    $email = normalize_email($email);
    if (filter_var($email, FILTER_VALIDATE_EMAIL) === false) {
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
        if ($ed === false) {
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
 * Check whether a magic-link token is currently valid (exists, unused,
 * unexpired) WITHOUT consuming it. Used by auth.php's GET handler to
 * decide between rendering the interstitial form vs. the expired page.
 * Email-scanner URL prefetch (Outlook Defender, Proofpoint, etc.) only
 * hits GET, so this read is a no-op for them — the token stays unused
 * until the actual user POSTs the form.
 */
function peek_magic_link(string $tok): bool
{
    if ($tok === '' || !ctype_xdigit($tok) || strlen($tok) !== 64) {
        return false;
    }
    $hash = hash_token($tok);
    $db = get_db();
    $stmt = $db->prepare(
        "SELECT 1 FROM editor_magic_link
         WHERE token_hash = ? AND used_at IS NULL AND expires_at > datetime('now')"
    );
    $stmt->execute([$hash]);
    return $stmt->fetchColumn() !== false;
}

/**
 * Consume a magic-link token. On success returns [editor_id, next_url]
 * and marks the token used. On failure returns null.
 *
 * @return array{editor_id: int, next_url: ?string}|null
 */
function consume_magic_link(string $tok): ?array
{
    if ($tok === '' || !ctype_xdigit($tok) || strlen($tok) !== 64) {
        return null;
    }
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
        if ($row === false) {
            $db->commit();
            return null;
        }

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
function safe_next_url(?string $next): string
{
    if ($next === null || $next === '') {
        return '/';
    }
    if (preg_match('#^/[^/\\\\]#', $next) !== 1) {
        return '/';
    }
    return $next;
}
