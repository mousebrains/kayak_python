<?php
declare(strict_types=1);
/**
 * PHPUnit bootstrap — shared test setup.
 *
 * Exposes ``kayak_test_pdo()``: returns a fresh in-memory PDO with just
 * enough schema to exercise auth / magic-link / session code. Starter
 * tests inline the CREATE TABLE they need; this helper saves the repeat.
 *
 * Also pre-installs a Config singleton with empty test data so any
 * test that touches the Config class (transitively via auth.php /
 * mail.php / turnstile.php / db.php) doesn't die_500 trying to read
 * /etc/kayak/runtime-config.json at the file-not-readable path. Tests
 * that need real config values override via ``Config::for_test($path)``
 * or ``Config::install_for_tests($data)``.
 *
 * The EDITOR_SESSION_COOKIE / EDITOR_CSRF_COOKIE / EDITOR_SESSION_DAYS
 * constants are defined by php/includes/auth.php. Every test that uses
 * them also requires auth.php, so they are always present when needed —
 * no bootstrap-side pre-definitions (those collide with auth.php's
 * `const` declarations under PHP 9).
 */

require_once __DIR__ . '/../../src/kayak/web/php/includes/config.php';
Config::install_for_tests([]);

// In-process tests must never let a handler's early-out `exit` kill the test
// run — KAYAK_TEST makes http_terminate() throw HttpExitException instead.
// No effect on integration tests (their `php -S` subprocess never sees it).
if (!defined('KAYAK_TEST')) {
    define('KAYAK_TEST', true);
}
require_once __DIR__ . '/../../src/kayak/web/php/includes/http_exit.php';

/** Fresh in-memory SQLite PDO with a minimal editor/magic-link/session schema. */
function kayak_test_pdo(): PDO {
    $pdo = new PDO('sqlite::memory:');
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $pdo->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);
    $pdo->exec('PRAGMA foreign_keys=ON');

    $pdo->exec("
        CREATE TABLE editor (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            display_name TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE editor_magic_link (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            editor_id INTEGER NOT NULL REFERENCES editor(id) ON DELETE CASCADE,
            token_hash TEXT UNIQUE NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at DATETIME NOT NULL,
            used_at DATETIME,
            ip_issued TEXT,
            next_url TEXT
        );
        CREATE TABLE editor_session (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            editor_id INTEGER NOT NULL REFERENCES editor(id) ON DELETE CASCADE,
            token_hash TEXT UNIQUE NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at DATETIME NOT NULL,
            last_seen_at DATETIME,
            ip TEXT,
            user_agent TEXT,
            revoked_at DATETIME
        );
    ");
    return $pdo;
}
