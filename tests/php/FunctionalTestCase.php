<?php

declare(strict_types=1);

use PHPUnit\Framework\TestCase;

/**
 * Base class for **in-process** functional tests of the PHP handlers.
 *
 * Unlike IntegrationTestCase (which spawns a `php -S` subprocess and drives it
 * over HTTP), this calls the handler functions directly in the test-runner
 * process. That has two payoffs:
 *   1. pcov sees the executed lines, so handler logic actually shows up in the
 *      coverage report (the subprocess approach is invisible to pcov).
 *   2. Edge branches (empty result sets, optional fields, 404/400) are cheap to
 *      drive by seeding rows + crafting params, no HTTP round-trip.
 *
 * Schema is seeded once per class by `levels init-db` against a tmp SQLite file
 * (same lockstep-with-models.py approach as IntegrationTestCase). The seeded
 * PDO is injected via `$GLOBALS['__kayak_test_db']` so both the handler-under-
 * test and the shared chrome (`current_editor`, …) hit the one DB. Early-out
 * `exit`s become catchable `HttpExitException`s via the KAYAK_TEST seam.
 *
 * Skipped (not failed) when the `levels` CLI isn't available, mirroring
 * IntegrationTestCase, so PHP-only contributors aren't blocked.
 */
abstract class FunctionalTestCase extends TestCase
{
    protected static ?PDO $pdo = null;
    private static string $dbPath = '';

    public static function setUpBeforeClass(): void
    {
        $repoRoot = dirname(__DIR__, 2);
        $levels = self::resolveVenvCommand($repoRoot);
        if ($levels === null) {
            self::markTestSkipped(
                'No `levels` CLI found — functional tests need it to seed the '
                . 'test DB schema via `levels init-db`.'
            );
            return;
        }

        $dbPath = tempnam(sys_get_temp_dir(), 'kayak_func_') . '.db';
        if (file_exists($dbPath)) {
            unlink($dbPath);
        }
        $output = [];
        $rc = 0;
        exec(
            sprintf(
                'DATABASE_URL=%s %s init-db 2>&1',
                escapeshellarg('sqlite:///' . $dbPath),
                escapeshellarg($levels),
            ),
            $output,
            $rc,
        );
        if ($rc !== 0 || !file_exists($dbPath)) {
            throw new RuntimeException(
                "`levels init-db` failed (rc=$rc): " . implode("\n", $output)
            );
        }
        self::$dbPath = $dbPath;

        $pdo = new PDO('sqlite:' . $dbPath);
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $pdo->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);
        $pdo->exec('PRAGMA foreign_keys=ON');
        static::seedDatabase($pdo);
        self::$pdo = $pdo;
    }

    public static function tearDownAfterClass(): void
    {
        self::$pdo = null;
        if (self::$dbPath !== '' && file_exists(self::$dbPath)) {
            unlink(self::$dbPath);
        }
        self::$dbPath = '';
    }

    protected function setUp(): void
    {
        if (self::$pdo === null) {
            self::markTestSkipped('test schema not available');
        }
        $GLOBALS['__kayak_test_db'] = self::$pdo;
        // Invalidate current_editor()'s process-static memo so a prior test's
        // editor/session can't leak into this one (and vice-versa on teardown).
        $GLOBALS['__kayak_editor_cache_gen'] = ($GLOBALS['__kayak_editor_cache_gen'] ?? 0) + 1;
        $_GET = [];
        $_POST = [];
        $_COOKIE = [];
        $_SERVER['REQUEST_METHOD'] = 'GET';
        $_SERVER['DOCUMENT_ROOT'] = dirname(__DIR__, 2) . '/public_html';
        $_SERVER['HTTP_HOST'] = 'levels.test';
        $_SERVER['REQUEST_URI'] = '/';
    }

    protected function tearDown(): void
    {
        unset($GLOBALS['__kayak_test_db']);
        $_COOKIE = [];
        $GLOBALS['__kayak_editor_cache_gen'] = ($GLOBALS['__kayak_editor_cache_gen'] ?? 0) + 1;
    }

    /** Override to seed reference + fixture rows once per class. */
    protected static function seedDatabase(PDO $db): void
    {
        // no-op by default
    }

    /** The seeded, injected PDO (asserts the schema is present). */
    protected function pdo(): PDO
    {
        $this->assertNotNull(self::$pdo);
        return self::$pdo;
    }

    /** Capture everything a handler echoes; rethrows on unexpected error. */
    protected function capture(callable $fn): string
    {
        ob_start();
        try {
            $fn();
            return (string) ob_get_clean();
        } catch (\Throwable $e) {
            ob_end_clean();
            throw $e;
        }
    }

    /**
     * Run a handler expecting it to early-out (404/400/403) and return the
     * thrown HttpExitException for status-code assertions. Fails if it returns.
     */
    protected function captureExit(callable $fn): HttpExitException
    {
        ob_start();
        try {
            $fn();
        } catch (HttpExitException $e) {
            ob_end_clean();
            return $e;
        } catch (\Throwable $e) {
            ob_end_clean();
            throw $e;
        }
        ob_end_clean();
        $this->fail('Expected HttpExitException, but the handler returned normally.');
    }

    /** Locate the `levels` CLI. Prefers the prod venv, then a local .venv, then PATH. */
    private static function resolveVenvCommand(string $repoRoot): ?string
    {
        $candidates = [
            '/home/pat/.venv/bin/levels',
            $repoRoot . '/.venv/bin/levels',
        ];
        foreach ($candidates as $candidate) {
            if (is_file($candidate) && is_executable($candidate)) {
                return $candidate;
            }
        }
        $whichOutput = [];
        $rc = 0;
        exec('which levels 2>/dev/null', $whichOutput, $rc);
        if ($rc === 0 && $whichOutput !== []) {
            return trim($whichOutput[0]);
        }
        return null;
    }
}
