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
        // auth.php transitively loads db.php which defines get_db() but
        // does NOT call it at load time — safe to include. Tests here
        // exercise pure helpers that don't touch the DB at all.
        require_once __DIR__ . '/../../php/includes/auth.php';
        require_once __DIR__ . '/../../php/includes/sanity.php';
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

    // -----------------------------------------------------------------
    // strip_html_tags — preserves user text, strips real tags
    // -----------------------------------------------------------------

    public function testStripTagsPlainText(): void
    {
        $this->assertSame('plain text', strip_html_tags('plain text'));
    }

    public function testStripTagsPreservesLessThanThree(): void
    {
        // Native PHP strip_tags would eat everything after "<".
        $this->assertSame('I love <3 boats', strip_html_tags('I love <3 boats'));
        $this->assertSame('<3', strip_html_tags('<3'));
    }

    public function testStripTagsPreservesInequalities(): void
    {
        $this->assertSame('x < y and y > z', strip_html_tags('x < y and y > z'));
    }

    public function testStripTagsPreservesBracketedEmail(): void
    {
        // "Name <foo@bar.com>" convention — @ isn't whitespace, so the run
        // after the tag name doesn't match, and the sequence is preserved.
        $this->assertSame('<foo@bar.com>', strip_html_tags('<foo@bar.com>'));
    }

    public function testStripTagsRemovesScript(): void
    {
        $this->assertSame('alert(1)', strip_html_tags('<script>alert(1)</script>'));
    }

    public function testStripTagsRemovesNestedReassembly(): void
    {
        // After one pass this would become "<script>alert(1)</script>" — the
        // loop re-strips until stable so the payload can't sneak through.
        $input = '<scr<script>ipt>alert(1)</scr</script>ipt>';
        $this->assertSame('alert(1)', strip_html_tags($input));
    }

    public function testStripTagsRemovesAttributes(): void
    {
        $this->assertSame('Hello', strip_html_tags('<p class="x">Hello</p>'));
        $this->assertSame('link', strip_html_tags('<a href="https://example.com">link</a>'));
    }

    public function testStripTagsRemovesComments(): void
    {
        $this->assertSame('visible', strip_html_tags('<!-- evil --> visible'));
    }

    public function testStripTagsPreservesUnclosedLessThan(): void
    {
        // No `>` terminator → not a tag, keep verbatim.
        $this->assertSame('unclosed <tag text', strip_html_tags('unclosed <tag text'));
    }

    public function testStripTagsMixedCaseTagname(): void
    {
        $this->assertSame('MIXED case', strip_html_tags('<TAG>MIXED case</TAG>'));
    }

    public function testStripTagsMultilineAttributes(): void
    {
        $this->assertSame('link', strip_html_tags("<a\n href=\"x\">link</a>"));
    }

    public function testStripTagsTrimsWhitespace(): void
    {
        $this->assertSame('trim me', strip_html_tags('   <b>trim me</b>   '));
    }
}
