<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/../../src/kayak/web/php/includes/prose.php';

/**
 * Unit tests for php/includes/prose.php (S3c).
 *
 * prose_fragment() resolves DATASET_DIR-rendered prose at
 * $_SERVER['DOCUMENT_ROOT']/prose/<page>.html; we point DOCUMENT_ROOT at a
 * scratch dir rather than serve over HTTP. Pure file read — no DB.
 */
final class ProseFragmentTest extends TestCase
{
    /** @var array<string, mixed> */
    private array $serverBackup;
    private string $tmp = '';

    protected function setUp(): void
    {
        $this->serverBackup = $_SERVER;
        $base = sys_get_temp_dir() . '/prose_test_' . getmypid() . '_' . uniqid();
        mkdir($base . '/prose', 0o777, true);
        $this->tmp = $base;
        $_SERVER['DOCUMENT_ROOT'] = $base;
    }

    protected function tearDown(): void
    {
        $_SERVER = $this->serverBackup;
        // Best-effort cleanup of the scratch docroot.
        foreach (glob($this->tmp . '/prose/*') ?: [] as $f) {
            @unlink($f);
        }
        @rmdir($this->tmp . '/prose');
        @rmdir($this->tmp);
    }

    public function test_returns_fragment_when_present(): void
    {
        $html = "<h2>About</h2>\n<p>Hello from the dataset.</p>\n";
        file_put_contents($this->tmp . '/prose/about.html', $html);
        $this->assertSame($html, prose_fragment('about'));
    }

    public function test_returns_null_when_absent(): void
    {
        $this->assertNull(prose_fragment('about'));
    }

    public function test_returns_null_when_docroot_missing(): void
    {
        // No DOCUMENT_ROOT → falls back to __DIR__/.. (the source tree, which has
        // no rendered prose/ dir) → null, never a warning.
        unset($_SERVER['DOCUMENT_ROOT']);
        $this->assertNull(prose_fragment('about'));
    }

    public function test_each_page_resolves_independently(): void
    {
        file_put_contents($this->tmp . '/prose/privacy.html', '<p>privacy</p>');
        $this->assertSame('<p>privacy</p>', prose_fragment('privacy'));
        $this->assertNull(prose_fragment('disclaimer'));
    }
}
