<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/../../php/includes/source_url.php';

/**
 * Unit tests for php/includes/source_url.php.
 *
 * sanitize_source_url() / source_url_from_referrer() read $_SERVER
 * (HTTP_HOST, HTTP_REFERER); we set those directly rather than over HTTP.
 * Pure-ish helpers — no DB.
 */
final class SourceUrlTest extends TestCase
{
    /** @var array<string, mixed> */
    private array $serverBackup;

    protected function setUp(): void
    {
        $this->serverBackup = $_SERVER;
        $_SERVER['HTTP_HOST'] = 'levels.wkcc.org';
        unset($_SERVER['HTTP_REFERER']);
    }

    protected function tearDown(): void
    {
        $_SERVER = $this->serverBackup;
    }

    // --- sanitize_source_url ---------------------------------------------

    public function test_empty_returns_empty(): void
    {
        $this->assertSame('', sanitize_source_url(''));
        $this->assertSame('', sanitize_source_url('   '));
    }

    public function test_relative_path_passes_through(): void
    {
        $this->assertSame('/description.php?id=42', sanitize_source_url('/description.php?id=42'));
        // Leading/trailing whitespace is trimmed.
        $this->assertSame('/reach.php', sanitize_source_url('  /reach.php  '));
    }

    public function test_same_origin_absolute_accepted(): void
    {
        $url = 'https://levels.wkcc.org/gauge.php?id=7';
        $this->assertSame($url, sanitize_source_url($url));
    }

    public function test_same_origin_ignores_port_on_self_host(): void
    {
        // HTTP_HOST carries host:port; the :port is stripped before compare.
        $_SERVER['HTTP_HOST'] = 'levels.wkcc.org:8443';
        $url = 'https://levels.wkcc.org/x';
        $this->assertSame($url, sanitize_source_url($url));
    }

    public function test_host_match_is_case_insensitive(): void
    {
        $url = 'https://LEVELS.WKCC.ORG/x';
        $this->assertSame($url, sanitize_source_url($url));
    }

    public function test_cross_origin_absolute_rejected(): void
    {
        $this->assertSame('', sanitize_source_url('https://evil.example/pwn'));
    }

    public function test_dangerous_schemes_rejected(): void
    {
        // A tampered hidden field must not store a clickable XSS URI: parse_url
        // gives these no host, so they used to slip through the relative-path
        // branch. The scheme check (case-insensitive) rejects them.
        $this->assertSame('', sanitize_source_url('javascript:alert(1)'));
        $this->assertSame('', sanitize_source_url('JaVaScRiPt:alert(1)'));
        $this->assertSame('', sanitize_source_url('data:text/html,<script>alert(1)</script>'));
        $this->assertSame('', sanitize_source_url('vbscript:msgbox(1)'));
        // An embedded TAB defeats parse_url's scheme detection, so without the
        // control-char filter these fall through as a relative path; the browser
        // then strips the tab on click → a live javascript:/data: href. (round-5 R1.3)
        $this->assertSame('', sanitize_source_url("j\tavascript:alert(1)"));
        $this->assertSame('', sanitize_source_url("da\tta:text/html,<script>alert(1)</script>"));
        // Legit schemes still pass, in any case.
        $this->assertSame('HTTPS://levels.wkcc.org/x', sanitize_source_url('HTTPS://levels.wkcc.org/x'));
    }

    public function test_crlf_nul_injection_rejected(): void
    {
        // CR/LF/NUL would splice email headers — rejected outright.
        $this->assertSame('', sanitize_source_url("/x\r\nBcc: a@b.com"));
        $this->assertSame('', sanitize_source_url("/x\nfoo"));
        $this->assertSame('', sanitize_source_url("/x\0bar"));
    }

    public function test_overlong_rejected(): void
    {
        // > 2048 chars → unusable.
        $this->assertSame('', sanitize_source_url('/' . str_repeat('a', 2048)));
    }

    public function test_at_2048_boundary_is_kept(): void
    {
        // Exactly 2048 chars survives the length guard (strict > test).
        $url = '/' . str_repeat('a', 2047); // total length 2048
        $this->assertSame(2048, strlen($url));
        $this->assertSame($url, sanitize_source_url($url));
    }

    public function test_unparseable_url_returns_empty(): void
    {
        // parse_url returns false for a malformed URL with a bad port.
        $this->assertSame('', sanitize_source_url('http://example.com:port'));
    }

    // --- source_url_from_referrer ----------------------------------------

    public function test_referrer_used_when_same_origin(): void
    {
        $_SERVER['HTTP_REFERER'] = 'https://levels.wkcc.org/reach.php?id=9';
        $this->assertSame(
            'https://levels.wkcc.org/reach.php?id=9',
            source_url_from_referrer('/contact.php')
        );
    }

    public function test_referrer_matching_self_path_is_dropped(): void
    {
        // A reload-from-form (referrer path == the form's own path) must not
        // overwrite the original source — returns ''.
        $_SERVER['HTTP_REFERER'] = 'https://levels.wkcc.org/contact.php';
        $this->assertSame('', source_url_from_referrer('/contact.php'));
    }

    public function test_no_referrer_returns_empty(): void
    {
        unset($_SERVER['HTTP_REFERER']);
        $this->assertSame('', source_url_from_referrer('/contact.php'));
    }

    public function test_cross_origin_referrer_returns_empty(): void
    {
        $_SERVER['HTTP_REFERER'] = 'https://evil.example/snoop';
        $this->assertSame('', source_url_from_referrer('/contact.php'));
    }
}
