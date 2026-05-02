<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

/**
 * _turnstile_env() env/$_SERVER precedence — PHP gets secrets through
 * getenv() (set by PHP-FPM pool env from /etc/kayak/secrets.env), with a
 * $_SERVER fallback for the legacy nginx fastcgi_param path.
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
        unset($_SERVER['TURNSTILE_SECRET']);
    }

    public function testEmptyWhenNothingSet(): void
    {
        $this->assertSame('', _turnstile_env('TURNSTILE_SECRET'));
    }

    public function testGetenvPreferredOverServer(): void
    {
        putenv('TURNSTILE_SECRET=from-env');
        $_SERVER['TURNSTILE_SECRET'] = 'from-server';
        $this->assertSame('from-env', _turnstile_env('TURNSTILE_SECRET'));
    }

    public function testServerFallbackWhenGetenvMissing(): void
    {
        putenv('TURNSTILE_SECRET');  // clear
        $_SERVER['TURNSTILE_SECRET'] = 'server-only';
        $this->assertSame('server-only', _turnstile_env('TURNSTILE_SECRET'));
    }

    public function testServerFallbackWhenGetenvEmpty(): void
    {
        putenv('TURNSTILE_SECRET=');  // set but empty
        $_SERVER['TURNSTILE_SECRET'] = 'server-fallback';
        $this->assertSame('server-fallback', _turnstile_env('TURNSTILE_SECRET'));
    }
}
