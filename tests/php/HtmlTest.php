<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/../../src/kayak/web/php/includes/html.php';

/**
 * Unit tests for autolink_urls() in php/includes/html.php.
 * Pure function — no DB, no HTTP.
 */
final class HtmlTest extends TestCase
{
    public function test_plain_text_is_escaped_no_links(): void
    {
        $this->assertSame('a &amp; b &lt;c&gt;', autolink_urls('a & b <c>'));
    }

    public function test_empty_string(): void
    {
        $this->assertSame('', autolink_urls(''));
    }

    public function test_bare_url_becomes_anchor(): void
    {
        $out = autolink_urls('see https://example.com/page now');
        $this->assertStringContainsString(
            '<a href="https://example.com/page" target="_blank" rel="noopener">https://example.com/page</a>',
            $out
        );
        // Surrounding text preserved.
        $this->assertStringStartsWith('see ', $out);
        $this->assertStringEndsWith(' now', $out);
    }

    public function test_http_and_https_both_linked(): void
    {
        $this->assertStringContainsString('<a href="http://x.test"', autolink_urls('http://x.test'));
        $this->assertStringContainsString('<a href="https://x.test"', autolink_urls('https://x.test'));
    }

    public function test_query_ampersands_survive_intact(): void
    {
        // The href must carry the escaped ampersand (so the link works), and
        // the URL text is escaped exactly once — no double-encoding.
        $out = autolink_urls('https://x.test/?a=1&b=2');
        $this->assertStringContainsString('href="https://x.test/?a=1&amp;b=2"', $out);
        $this->assertStringNotContainsString('&amp;amp;', $out);
    }

    public function test_trailing_punctuation_excluded_from_url(): void
    {
        // A sentence-ending period must not be swallowed into the href.
        $out = autolink_urls('Visit https://example.com.');
        $this->assertStringContainsString('href="https://example.com"', $out);
        // The period lands outside the anchor as escaped text.
        $this->assertStringEndsWith('</a>.', $out);
    }

    public function test_url_in_parens_strips_trailing_paren(): void
    {
        $out = autolink_urls('(https://example.com/x)');
        $this->assertStringContainsString('href="https://example.com/x"', $out);
        $this->assertStringEndsWith('</a>)', $out);
    }

    public function test_surrounding_text_html_is_escaped_not_url(): void
    {
        // The "<b>" around the link is escaped; the link itself is anchored.
        $out = autolink_urls('<b>https://x.test</b>');
        $this->assertStringStartsWith('&lt;b&gt;', $out);
        $this->assertStringContainsString('<a href="https://x.test"', $out);
        $this->assertStringEndsWith('&lt;/b&gt;', $out);
    }

    public function test_multiple_urls_all_linked(): void
    {
        $out = autolink_urls('a https://one.test b https://two.test c');
        $this->assertSame(2, substr_count($out, '<a href='));
        $this->assertStringContainsString('href="https://one.test"', $out);
        $this->assertStringContainsString('href="https://two.test"', $out);
    }

    public function test_naked_url_only_yields_just_anchor(): void
    {
        $out = autolink_urls('https://example.com');
        $this->assertSame(
            '<a href="https://example.com" target="_blank" rel="noopener">https://example.com</a>',
            $out
        );
    }
}
