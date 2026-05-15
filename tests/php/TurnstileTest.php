<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

/**
 * turnstile_site_key() / turnstile_secret() resolve via Config.
 *
 * Config::str reads the JSON snapshot first, then falls back to
 * getenv(strtoupper($key)). When neither source supplies a value, the
 * helpers return an empty string and turnstile_enabled() reports false.
 *
 * Tests don't write a runtime-config.json on disk; the Config singleton
 * loads, fails to find the file, logs one [CONFIG-FALLBACK] line per
 * test run, and the getenv chain takes over.
 */
final class TurnstileTest extends TestCase
{
    public static function setUpBeforeClass(): void
    {
        require_once __DIR__ . '/../../php/includes/turnstile.php';
    }

    protected function tearDown(): void
    {
        // Clean up any env we set so tests don't leak into each other.
        putenv('TURNSTILE_SECRET');
        putenv('TURNSTILE_SITE_KEY');
        Config::reset_for_test();
    }

    public function testEmptyWhenNothingSet(): void
    {
        $this->assertSame('', turnstile_secret());
        $this->assertSame('', turnstile_site_key());
    }

    public function testGetenvSuppliesValueWhenJsonMissing(): void
    {
        // Default Config path (/etc/kayak/runtime-config.json) typically
        // exists on a deployed host but isn't readable by the test runner;
        // Config::str falls back to getenv. (The test runner deliberately
        // avoids writing a JSON file — see the class docblock.)
        putenv('TURNSTILE_SECRET=from-env');
        putenv('TURNSTILE_SITE_KEY=site-key-from-env');
        $this->assertSame('from-env', turnstile_secret());
        $this->assertSame('site-key-from-env', turnstile_site_key());
    }

    public function testJsonValueWinsOverEnv(): void
    {
        $tmp = tempnam(sys_get_temp_dir(), 'kayak-config-');
        $this->assertNotFalse($tmp);
        file_put_contents($tmp, json_encode([
            'turnstile_secret'   => 'from-json',
            'turnstile_site_key' => 'site-key-from-json',
        ]));
        try {
            Config::for_test($tmp);
            putenv('TURNSTILE_SECRET=from-env');
            putenv('TURNSTILE_SITE_KEY=site-key-from-env');
            $this->assertSame('from-json', turnstile_secret());
            $this->assertSame('site-key-from-json', turnstile_site_key());
        } finally {
            unlink($tmp);
        }
    }
}
