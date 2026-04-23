<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

/**
 * Guards the `_sqlite_path()` helper in php/includes/db.php.
 *
 * The helper used to be inlined into `get_db()` as
 * `dirname(__DIR__, 2) . '/../DB/kayak.db'` which resolved correctly on
 * dev (kayak/php/includes/ is 2 dirs deep from the repo root) but to
 * /home/DB/kayak.db on prod (public_html/includes/ is 1 dir deep under
 * /home/pat). This test pins the new `dirname(__DIR__, 3) . '/DB/kayak.db'`
 * form that works in both layouts, and confirms SQLITE_PATH still wins.
 */
final class DbFallbackTest extends TestCase
{
    public static function setUpBeforeClass(): void
    {
        require_once __DIR__ . '/../../php/includes/db.php';
    }

    protected function setUp(): void
    {
        // Ensure no stale env bleeds between tests.
        putenv('SQLITE_PATH');
    }

    public function testEnvTakesPriority(): void
    {
        putenv('SQLITE_PATH=/tmp/kayak-test-env.db');
        $this->assertSame('/tmp/kayak-test-env.db', _sqlite_path());
    }

    public function testFallbackRelativeToDbPhp(): void
    {
        // With no SQLITE_PATH, the path should be relative to this project's
        // db.php — which lives at <repo>/php/includes/db.php. dirname(__DIR__, 3)
        // from that file resolves to <repo>'s parent (/Users/pat/tpw on dev,
        // /home/pat on prod).
        putenv('SQLITE_PATH');
        $expected_parent = dirname(dirname(__DIR__));  // <repo>/php -> <repo>
        $expected = dirname($expected_parent) . '/DB/kayak.db';
        $this->assertSame($expected, _sqlite_path());
    }

    public function testFallbackResolvesToRepoParentDB(): void
    {
        // Sanity check the two-layout claim: on this machine the repo is
        // /Users/pat/tpw/kayak, so the fallback should land in /Users/pat/tpw/DB.
        putenv('SQLITE_PATH');
        $path = _sqlite_path();
        $this->assertStringEndsWith('/DB/kayak.db', $path);
        // Path is absolute (starts with /) so PDO can open it directly.
        $this->assertStringStartsWith('/', $path);
    }
}
