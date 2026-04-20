<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

/**
 * _hcaptcha_env() env/$_SERVER precedence — the core guarantee that T2-14
 * relies on. PHP gets secrets through getenv() (set by PHP-FPM pool env
 * from /etc/kayak/secrets.env), falling back to $_SERVER (legacy nginx
 * fastcgi_param path).
 */
final class HcaptchaTest extends TestCase
{
    public static function setUpBeforeClass(): void
    {
        require_once __DIR__ . '/../../php/includes/hcaptcha.php';
    }

    protected function tearDown(): void
    {
        // Clean up any env we set so tests don't leak into each other.
        putenv('HCAPTCHA_SECRET');
        unset($_SERVER['HCAPTCHA_SECRET']);
    }

    public function testEmptyWhenNothingSet(): void
    {
        $this->assertSame('', _hcaptcha_env('HCAPTCHA_SECRET'));
    }

    public function testGetenvPreferredOverServer(): void
    {
        putenv('HCAPTCHA_SECRET=from-env');
        $_SERVER['HCAPTCHA_SECRET'] = 'from-server';
        $this->assertSame('from-env', _hcaptcha_env('HCAPTCHA_SECRET'));
    }

    public function testServerFallbackWhenGetenvMissing(): void
    {
        putenv('HCAPTCHA_SECRET');  // clear
        $_SERVER['HCAPTCHA_SECRET'] = 'server-only';
        $this->assertSame('server-only', _hcaptcha_env('HCAPTCHA_SECRET'));
    }

    public function testServerFallbackWhenGetenvEmpty(): void
    {
        putenv('HCAPTCHA_SECRET=');  // set but empty
        $_SERVER['HCAPTCHA_SECRET'] = 'server-fallback';
        $this->assertSame('server-fallback', _hcaptcha_env('HCAPTCHA_SECRET'));
    }
}
