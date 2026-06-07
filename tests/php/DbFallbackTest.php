<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

/**
 * Guards the `_sqlite_path()` helper in src/kayak/web/php/includes/db.php.
 *
 * The fallback is keyed to where db.php is *deployed* — `levels build` copies the
 * PHP layer to <docroot>/includes/db.php, so `dirname(__DIR__, 2) . '/DB/kayak.db'`
 * resolves to the deploy root's parent + /DB (e.g. /home/pat/public_html/includes
 * -> /home/pat/DB), the sibling-of-deploy-root convention shared with
 * backup/decimate. (The PHP source moved deeper, to src/kayak/web/php/includes,
 * in S4a-2 slice B2; the level was corrected from the old `dirname(__DIR__, 3)`,
 * which the move surfaced as wrong for the deployed layout.) This test pins the
 * level + suffix and confirms SQLITE_PATH still wins.
 */
final class DbFallbackTest extends TestCase
{
    public static function setUpBeforeClass(): void
    {
        require_once __DIR__ . '/../../src/kayak/web/php/includes/db.php';
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
        // No SQLITE_PATH: the fallback is dirname(<db.php dir>, 2) . '/DB/kayak.db'
        // (the sibling-of-deploy-root convention). Derive the expected value from
        // db.php's real location so the test tracks the file wherever it lives and
        // pins the dirname level (2) + suffix.
        putenv('SQLITE_PATH');
        $db_php_dir = (string) realpath(__DIR__ . '/../../src/kayak/web/php/includes');
        $expected = dirname($db_php_dir, 2) . '/DB/kayak.db';
        $this->assertSame($expected, _sqlite_path());
    }

    public function testFallbackResolvesToSiblingDB(): void
    {
        // The fallback must be an absolute path ending in /DB/kayak.db so PDO can
        // open it directly.
        putenv('SQLITE_PATH');
        $path = _sqlite_path();
        $this->assertStringEndsWith('/DB/kayak.db', $path);
        $this->assertStringStartsWith('/', $path);
    }
}
