<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

/**
 * Unit tests for the URL-validation primitives in php/includes/auth.php.
 * Pure functions — no DB, no HTTP.
 */
final class SanityTest extends TestCase
{
    public static function setUpBeforeClass(): void
    {
        // auth.php pulls in db.php; tests exercise the URL helper only, but
        // we still need db.php's get_db() not to trigger a real DB open.
        // Stubbing via a dummy function declared before auth.php loads does
        // the job.
        if (!function_exists('get_db')) {
            // phpcs:ignore Generic.Functions.FunctionCallArgumentSpacing
            eval('function get_db(): PDO { return new PDO("sqlite::memory:"); }');
        }
        require_once __DIR__ . '/../../php/includes/auth.php';
    }

    public function testNullReturnsRoot(): void
    {
        $this->assertSame('/', safe_next_url(null));
    }

    public function testEmptyReturnsRoot(): void
    {
        $this->assertSame('/', safe_next_url(''));
    }

    public function testSameOriginPathIsAccepted(): void
    {
        $this->assertSame('/reach.php?id=42', safe_next_url('/reach.php?id=42'));
    }

    public function testProtocolRelativeRejected(): void
    {
        // //evil.example/ is protocol-relative — browsers would send the
        // user off-site. Must not survive validation.
        $this->assertSame('/', safe_next_url('//evil.example/pwn'));
    }

    public function testAbsoluteHttpsRejected(): void
    {
        $this->assertSame('/', safe_next_url('https://evil.example/'));
    }

    public function testJavascriptSchemeRejected(): void
    {
        $this->assertSame('/', safe_next_url('javascript:alert(1)'));
    }

    public function testRelativePathWithoutLeadingSlashRejected(): void
    {
        $this->assertSame('/', safe_next_url('reach.php?id=42'));
    }
}
