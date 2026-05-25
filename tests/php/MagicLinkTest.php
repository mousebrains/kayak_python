<?php

declare(strict_types=1);

require_once __DIR__ . '/FunctionalTestCase.php';
require_once __DIR__ . '/Fixtures.php';
require_once __DIR__ . '/../../php/includes/auth.php';
require_once __DIR__ . '/../../php/includes/auth_magic_link.php';

/**
 * Tier-1 security coverage for the magic-link login flow (auth_magic_link.php):
 * email normalization, issuance + rate-throttle, single-use consumption,
 * expiry, and post-login redirect safety. Runs in-process via the get_db()
 * seam so the issue/peek/consume functions (which call get_db() with no
 * override) hit the seeded test DB.
 */
final class MagicLinkTest extends FunctionalTestCase
{
    protected function setUp(): void
    {
        parent::setUp();
        $_SERVER['REMOTE_ADDR'] = '203.0.113.7';
    }

    public function testNormalizeEmailGmailRules(): void
    {
        $this->assertSame('foo@gmail.com', normalize_email('F.o.o+bar@googlemail.com'));
        $this->assertSame('foo@gmail.com', normalize_email('  Foo@Gmail.com '));
        // non-gmail: lowercased + trimmed, but local part left intact
        $this->assertSame('a.b+c@example.com', normalize_email('A.B+C@Example.com'));
        // no @ — returned lowercased as-is
        $this->assertSame('noat', normalize_email('NoAt'));
    }

    public function testSafeNextUrlBlocksOffsite(): void
    {
        $this->assertSame('/', safe_next_url(null));
        $this->assertSame('/', safe_next_url(''));
        $this->assertSame('/reach.php?id=1', safe_next_url('/reach.php?id=1'));
        $this->assertSame('/', safe_next_url('//evil.example/x'));   // protocol-relative
        $this->assertSame('/', safe_next_url('/\\evil.example'));    // backslash → // after normalization
        $this->assertSame('/', safe_next_url('https://evil.example')); // not a leading single slash
    }

    public function testIssueCreatesPendingEditorAndStoresOnlyHash(): void
    {
        $res = issue_magic_link('newuser@example.com');
        $this->assertFalse($res['banned']);
        $this->assertGreaterThan(0, $res['editor_id']);
        $this->assertSame(64, strlen($res['token']));

        $status = $this->pdo()->query(
            "SELECT status FROM editor WHERE id = {$res['editor_id']}"
        )->fetchColumn();
        $this->assertSame('pending', $status);

        // The hash is stored; the raw token never is.
        $byHash = $this->pdo()->prepare('SELECT COUNT(*) FROM editor_magic_link WHERE token_hash = ?');
        $byHash->execute([hash_token($res['token'])]);
        $this->assertSame(1, (int) $byHash->fetchColumn());
        $byRaw = $this->pdo()->prepare('SELECT COUNT(*) FROM editor_magic_link WHERE token_hash = ?');
        $byRaw->execute([$res['token']]);
        $this->assertSame(0, (int) $byRaw->fetchColumn());
    }

    public function testIssueInvalidEmailThrows(): void
    {
        $this->expectException(RuntimeException::class);
        issue_magic_link('not-an-email');
    }

    public function testIssueBannedEditorReturnsBannedNoToken(): void
    {
        Fixtures::editor($this->pdo(), ['email' => 'banned@example.com', 'status' => 'banned']);
        $res = issue_magic_link('banned@example.com');
        $this->assertTrue($res['banned']);
        $this->assertSame('', $res['token']);
    }

    public function testIssueThrottlesAfterEmailCap(): void
    {
        for ($i = 0; $i < 5; $i++) {
            $this->assertFalse(issue_magic_link('rate@example.com')['banned'], "issue $i");
        }
        $res = issue_magic_link('rate@example.com');   // 6th within the hour
        $this->assertTrue($res['banned']);
        $this->assertSame('', $res['token']);
    }

    public function testPeekDoesNotConsume(): void
    {
        $res = issue_magic_link('peek@example.com');
        $this->assertTrue(peek_magic_link($res['token']));
        $this->assertNotNull(consume_magic_link($res['token']));   // still usable after a peek
    }

    public function testConsumeIsSingleUseAndCarriesNext(): void
    {
        $res = issue_magic_link('consume@example.com', '/account.php');
        $first = consume_magic_link($res['token']);
        $this->assertNotNull($first);
        $this->assertSame($res['editor_id'], $first['editor_id']);
        $this->assertSame('/account.php', $first['next_url']);

        $this->assertNull(consume_magic_link($res['token']));      // single-use
        $this->assertFalse(peek_magic_link($res['token']));        // now used
    }

    public function testConsumeAndPeekRejectGarbageAndExpired(): void
    {
        $this->assertNull(consume_magic_link(''));
        $this->assertNull(consume_magic_link('short'));
        $this->assertNull(consume_magic_link(str_repeat('z', 64)));  // 64 chars but non-hex
        $this->assertFalse(peek_magic_link(str_repeat('z', 64)));

        $ed = Fixtures::editor($this->pdo(), ['email' => 'exp@example.com']);
        $tok = generate_token();
        $this->pdo()->prepare(
            "INSERT INTO editor_magic_link (editor_id, token_hash, created_at, expires_at)
             VALUES (?, ?, datetime('now'), datetime('now', '-1 hour'))"
        )->execute([$ed, hash_token($tok)]);
        $this->assertFalse(peek_magic_link($tok));
        $this->assertNull(consume_magic_link($tok));
    }
}
