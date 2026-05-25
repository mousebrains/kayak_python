<?php

declare(strict_types=1);

require_once __DIR__ . '/FunctionalTestCase.php';
require_once __DIR__ . '/Fixtures.php';
require_once __DIR__ . '/../../php/includes/auth.php';

/**
 * Tier-1 security coverage for auth.php: token helpers, session create/resolve/
 * revoke, the current_editor() rejection branches (malformed cookie / expired /
 * revoked / banned), cookie flags, CSRF double-submit, and the feature/editor/
 * maintainer gates. In-process via the get_db() seam; the 403/404/302 early-outs
 * are assertable through http_terminate (captureExit).
 */
final class AuthSessionTest extends FunctionalTestCase
{
    public function testTokenHelpers(): void
    {
        $t = generate_token();
        $this->assertSame(64, strlen($t));
        $this->assertTrue(ctype_xdigit($t));
        $this->assertSame(hash('sha256', $t), hash_token($t));
        $this->assertNotSame(generate_token(), generate_token());
        $this->assertSame(8, strlen(generate_token(4)));   // 4 bytes → 8 hex chars
    }

    public function testSetSessionCreatesRowAndCurrentEditorResolves(): void
    {
        $ed = Fixtures::editor($this->pdo(), ['email' => 'sess@example.com', 'status' => 'full']);
        $tok = set_editor_session($ed);
        $this->assertSame(64, strlen($tok));
        $this->assertSame($tok, $_COOKIE[EDITOR_SESSION_COOKIE]);
        $this->assertArrayHasKey(EDITOR_CSRF_COOKIE, $_COOKIE);   // CSRF rotated on login

        $cur = current_editor($this->pdo());
        $this->assertNotNull($cur);
        $this->assertSame('sess@example.com', $cur['email']);

        $this->assertSame(1, (int) $this->pdo()->query(
            "SELECT COUNT(*) FROM editor_session WHERE editor_id = $ed AND revoked_at IS NULL"
        )->fetchColumn());
        $this->assertNotNull($this->pdo()->query("SELECT last_login_at FROM editor WHERE id = $ed")->fetchColumn());
    }

    public function testCurrentEditorRejectsMalformedCookie(): void
    {
        foreach (['', 'short', str_repeat('z', 64)] as $bad) {
            $_COOKIE[EDITOR_SESSION_COOKIE] = $bad;
            $this->assertNull(current_editor($this->pdo()), "cookie=[$bad]");
        }
    }

    public function testCurrentEditorRejectsExpiredRevokedBanned(): void
    {
        $cases = [
            ['full',   "datetime('now','-1 day')", 'NULL'],            // expired
            ['full',   "datetime('now','+1 day')", "datetime('now')"], // revoked
            ['banned', "datetime('now','+1 day')", 'NULL'],            // banned editor
        ];
        foreach ($cases as $i => [$status, $exp, $rev]) {
            $ed = Fixtures::editor($this->pdo(), ['email' => "edge$i@example.com", 'status' => $status]);
            $tok = generate_token();
            $this->pdo()->exec(
                "INSERT INTO editor_session (editor_id, token_hash, created_at, expires_at, revoked_at)
                 VALUES ($ed, '" . hash_token($tok) . "', datetime('now'), $exp, $rev)"
            );
            $_COOKIE[EDITOR_SESSION_COOKIE] = $tok;
            $this->assertNull(current_editor($this->pdo()), "case $i");
        }
    }

    public function testClearSessionRevokesAndClearsCookie(): void
    {
        $ed = Fixtures::editor($this->pdo(), ['email' => 'clr@example.com']);
        set_editor_session($ed);
        clear_editor_session($this->pdo());
        $this->assertArrayNotHasKey(EDITOR_SESSION_COOKIE, $_COOKIE);
        $this->assertSame(1, (int) $this->pdo()->query(
            "SELECT COUNT(*) FROM editor_session WHERE editor_id = $ed AND revoked_at IS NOT NULL"
        )->fetchColumn());
    }

    public function testCookieParams(): void
    {
        $p = _cookie_params(3600);
        $this->assertTrue($p['httponly']);
        $this->assertSame('Strict', $p['samesite']);
        $this->assertSame('/', $p['path']);
        $this->assertFalse($p['secure']);
        $this->assertGreaterThan(time(), $p['expires']);
        $this->assertSame(0, _cookie_params(0)['expires']);
        $_SERVER['HTTPS'] = 'on';
        $this->assertTrue(_cookie_params(0)['secure']);
        unset($_SERVER['HTTPS']);
    }

    public function testCsrfTokenGeneratesAndReuses(): void
    {
        unset($_COOKIE[EDITOR_CSRF_COOKIE]);
        $t1 = csrf_token();
        $this->assertSame(64, strlen($t1));
        $this->assertSame($t1, $_COOKIE[EDITOR_CSRF_COOKIE]);
        $this->assertSame($t1, csrf_token());   // reused from the cookie, not regenerated
    }

    public function testRequireCsrfAcceptsMatchRejectsOtherwise(): void
    {
        $tok = generate_token();
        $_COOKIE[EDITOR_CSRF_COOKIE] = $tok;
        $_POST['csrf_token'] = $tok;
        require_csrf();
        $this->addToAssertionCount(1);   // reached here = accepted

        $_POST['csrf_token'] = generate_token();   // mismatch
        $this->assertSame(403, $this->captureExit(fn() => require_csrf())->statusCode);

        $_POST = [];   // missing
        $this->assertSame(403, $this->captureExit(fn() => require_csrf())->statusCode);
    }

    public function testIsMaintainer(): void
    {
        $this->assertTrue(is_maintainer(['status' => 'maintainer']));
        $this->assertFalse(is_maintainer(['status' => 'full']));
        $this->assertFalse(is_maintainer(null));   // no session
    }

    public function testEditorFeatureGate404WhenOff(): void
    {
        $this->assertFalse(editor_feature_enabled());
        $this->assertSame(404, $this->captureExit(fn() => require_editor_feature())->statusCode);
    }

    public function testRequireEditorRedirectsWhenAnonymous(): void
    {
        $this->assertSame(302, $this->captureExit(fn() => require_editor())->statusCode);
    }

    public function testRequireMaintainerForbidsNonMaintainer(): void
    {
        set_editor_session(Fixtures::editor($this->pdo(), ['email' => 'plain@example.com', 'status' => 'full']));
        $this->assertSame(403, $this->captureExit(fn() => require_maintainer())->statusCode);
    }

    public function testRequireMaintainerAllowsMaintainer(): void
    {
        set_editor_session(Fixtures::editor($this->pdo(), ['email' => 'boss@example.com', 'status' => 'maintainer']));
        $this->assertSame('boss@example.com', require_maintainer()['email']);
    }
}
