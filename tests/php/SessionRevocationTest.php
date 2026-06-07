<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

/**
 * Regression coverage for F-15.
 *
 * Tier 1 audit verified statically that current_editor() filters
 *   `WHERE s.revoked_at IS NULL AND s.expires_at > datetime('now')
 *    AND e.status != 'banned'`
 * so a logged-out cookie cannot be replayed. This test exercises the
 * filter dynamically so a future refactor that drops any of those
 * clauses fails CI loudly.
 *
 * The PDO is injected via the `$db_override` parameter that
 * current_editor() / clear_editor_session() accept for testing — the
 * production-side get_db() singleton is never touched here.
 */
final class SessionRevocationTest extends TestCase
{
    public static function setUpBeforeClass(): void
    {
        require_once __DIR__ . '/../../src/kayak/web/php/includes/auth.php';
    }

    protected function setUp(): void
    {
        // Each test starts with no cookies; if a prior test set the
        // session cookie globally, clear it.
        unset($_COOKIE[EDITOR_SESSION_COOKIE]);
        unset($_COOKIE[EDITOR_CSRF_COOKIE]);
        $_SERVER['REMOTE_ADDR'] = '127.0.0.1';
        $_SERVER['HTTP_USER_AGENT'] = 'phpunit';
    }

    /** Insert an editor + a fresh (unrevoked, unexpired) session row. Returns the cookie value. */
    private function seedLiveSession(PDO $db, string $email, string $status = 'minimal'): string
    {
        $db->prepare("INSERT INTO editor (email, status) VALUES (?, ?)")
            ->execute([$email, $status]);
        $editor_id = (int)$db->lastInsertId();

        $tok = bin2hex(random_bytes(32));
        $hash = hash('sha256', $tok);
        $db->prepare(
            "INSERT INTO editor_session
                (editor_id, token_hash, expires_at, last_seen_at)
             VALUES (?, ?, datetime('now', '+7 days'), datetime('now'))"
        )->execute([$editor_id, $hash]);

        return $tok;
    }

    public function testLiveSessionReturnsEditor(): void
    {
        $db = kayak_test_pdo();
        $tok = $this->seedLiveSession($db, 'pat@example.com');
        $_COOKIE[EDITOR_SESSION_COOKIE] = $tok;

        $ed = current_editor($db);

        $this->assertNotNull($ed, 'live session should resolve to an editor');
        $this->assertSame('pat@example.com', $ed['email']);
    }

    public function testRevokedSessionReturnsNull(): void
    {
        $db = kayak_test_pdo();
        $tok = $this->seedLiveSession($db, 'pat@example.com');
        $_COOKIE[EDITOR_SESSION_COOKIE] = $tok;

        // Sanity: live first.
        $this->assertNotNull(current_editor($db));

        // Now logout — clear_editor_session sets revoked_at on the session row.
        clear_editor_session($db);

        // Restore the cookie (clear_editor_session unsets it; we simulate
        // an attacker replaying a stolen cookie value).
        $_COOKIE[EDITOR_SESSION_COOKIE] = $tok;

        $this->assertNull(
            current_editor($db),
            'a revoked session cookie must not authenticate the request — guards F-15'
        );
    }

    public function testExpiredSessionReturnsNull(): void
    {
        $db = kayak_test_pdo();
        $db->prepare("INSERT INTO editor (email, status) VALUES ('expired@example.com', 'minimal')")
            ->execute();
        $editor_id = (int)$db->lastInsertId();

        $tok = bin2hex(random_bytes(32));
        $hash = hash('sha256', $tok);
        $db->prepare(
            "INSERT INTO editor_session
                (editor_id, token_hash, expires_at)
             VALUES (?, ?, datetime('now', '-1 minute'))"
        )->execute([$editor_id, $hash]);
        $_COOKIE[EDITOR_SESSION_COOKIE] = $tok;

        $this->assertNull(
            current_editor($db),
            'expired sessions must not authenticate'
        );
    }

    public function testBannedEditorReturnsNull(): void
    {
        $db = kayak_test_pdo();
        $tok = $this->seedLiveSession($db, 'baddie@example.com', 'banned');
        $_COOKIE[EDITOR_SESSION_COOKIE] = $tok;

        $this->assertNull(
            current_editor($db),
            'a banned editor must not authenticate even with a live session row'
        );
    }

    public function testMissingCookieReturnsNull(): void
    {
        $db = kayak_test_pdo();
        // No $_COOKIE setup — simulate a request with no session cookie.
        $this->assertNull(current_editor($db));
    }

    public function testMalformedCookieReturnsNull(): void
    {
        $db = kayak_test_pdo();
        $this->seedLiveSession($db, 'pat@example.com');

        // Garbage tokens (wrong length, non-hex) must be rejected without
        // ever hitting the DB.
        $_COOKIE[EDITOR_SESSION_COOKIE] = 'not-a-hex-token';
        $this->assertNull(current_editor($db));
    }
}
