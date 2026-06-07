<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

/**
 * Pins the auth primitives that the rewritten php/edit.php depends on.
 *
 * edit.php itself is a procedural controller that echoes HTML and calls
 * include_header / include_footer — awkward to instantiate in a unit
 * test. This suite covers the auth helpers edit.php leans on instead:
 *
 *   - hash_token() is a stable sha256 — the server stores the hash, not
 *     the raw cookie value.
 *   - is_maintainer() only accepts rows where status === 'maintainer'.
 *   - generate_token() produces 32-byte hex values.
 *   - safe_next_url() rejects off-site / protocol-relative redirects so
 *     the /edit.php -> /login.php bounce can't be weaponized.
 */
final class EditAuthTest extends TestCase
{
    public static function setUpBeforeClass(): void
    {
        require_once __DIR__ . '/../../src/kayak/web/php/includes/auth.php';
    }

    public function testHashTokenIsSha256(): void
    {
        $raw = 'abcd1234';
        $this->assertSame(hash('sha256', $raw), hash_token($raw));
    }

    public function testIsMaintainerRequiresExactStatus(): void
    {
        $this->assertTrue(is_maintainer(['status' => 'maintainer']));
        $this->assertFalse(is_maintainer(['status' => 'full']));
        $this->assertFalse(is_maintainer(['status' => 'minimal']));
        $this->assertFalse(is_maintainer(['status' => 'pending']));
        $this->assertFalse(is_maintainer(['status' => 'banned']));
        $this->assertFalse(is_maintainer([]));
        $this->assertFalse(is_maintainer(null));
    }

    public function testGenerateTokenShape(): void
    {
        $tok = generate_token();
        $this->assertSame(64, strlen($tok), '32 random bytes -> 64 hex chars');
        $this->assertTrue((bool)preg_match('/^[0-9a-f]{64}$/', $tok));

        // Custom byte-count also respected.
        $short = generate_token(8);
        $this->assertSame(16, strlen($short));
    }

    public function testSafeNextUrlRejectsOffsite(): void
    {
        // Empty / missing defaults to /
        $this->assertSame('/', safe_next_url(null));
        $this->assertSame('/', safe_next_url(''));

        // Protocol-relative URLs and absolute URLs are rejected — both
        // must NOT be allowed as the magic-link redirect target, since
        // they'd bounce a freshly-authed maintainer off-site.
        $this->assertSame('/', safe_next_url('//evil.example/'));
        $this->assertSame('/', safe_next_url('https://evil.example/path'));
        $this->assertSame('/', safe_next_url('javascript:alert(1)'));

        // Same-origin paths survive intact.
        $this->assertSame('/edit.php?id=42', safe_next_url('/edit.php?id=42'));
        $this->assertSame('/description.php?id=7', safe_next_url('/description.php?id=7'));
    }
}
