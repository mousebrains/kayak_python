<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

/**
 * turnstile_site_key() / turnstile_secret() resolve from Config.
 *
 * Phase 4 of T3.3 removed the getenv() fallback — Config::str returns
 * the JSON value or the wrapper's $default. Each test installs a
 * Config fixture via for_test() so the loader doesn't try to read
 * /etc/kayak/runtime-config.json (which would die_500 in-process).
 */
final class TurnstileTest extends TestCase
{
    public static function setUpBeforeClass(): void
    {
        require_once __DIR__ . '/../../php/includes/turnstile.php';
    }

    /** @var list<string> Temp JSON files to clean up after each test. */
    private array $tmpFiles = [];

    protected function tearDown(): void
    {
        // Restore bootstrap's empty Config — null'ing the singleton
        // would die_500 the next test class's first Config read.
        Config::install_for_tests([]);
        foreach ($this->tmpFiles as $f) {
            @unlink($f);
        }
        $this->tmpFiles = [];
    }

    /**
     * @param array<string, mixed> $data
     */
    private function _install_fixture(array $data): void
    {
        $tmp = tempnam(sys_get_temp_dir(), 'kayak-turnstile-test-');
        $this->assertNotFalse($tmp);
        $this->tmpFiles[] = $tmp;
        file_put_contents($tmp, json_encode($data));
        Config::for_test($tmp);
    }

    public function testEmptyWhenJsonHasNeitherKey(): void
    {
        // JSON exists but doesn't contain turnstile_* — the wrapper's
        // empty-string default applies and turnstile_enabled() returns false.
        $this->_install_fixture(['site_url' => 'https://example.com/']);
        $this->assertSame('', turnstile_secret());
        $this->assertSame('', turnstile_site_key());
        $this->assertFalse(turnstile_enabled());
    }

    public function testValuesFromJson(): void
    {
        $this->_install_fixture([
            'turnstile_secret'   => 'from-json',
            'turnstile_site_key' => 'site-key-from-json',
        ]);
        $this->assertSame('from-json', turnstile_secret());
        $this->assertSame('site-key-from-json', turnstile_site_key());
        $this->assertTrue(turnstile_enabled());
    }

    public function testEnvIgnoredWhenJsonProvidesValue(): void
    {
        // Phase 4 dropped the env fallback; setting env vars must NOT
        // affect the resolved values.
        $this->_install_fixture([
            'turnstile_secret'   => 'from-json',
            'turnstile_site_key' => 'site-key-from-json',
        ]);
        putenv('TURNSTILE_SECRET=from-env-ignored');
        putenv('TURNSTILE_SITE_KEY=site-key-from-env-ignored');
        try {
            $this->assertSame('from-json', turnstile_secret());
            $this->assertSame('site-key-from-json', turnstile_site_key());
        } finally {
            putenv('TURNSTILE_SECRET');
            putenv('TURNSTILE_SITE_KEY');
        }
    }
}
