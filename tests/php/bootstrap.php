<?php
declare(strict_types=1);
/**
 * PHPUnit bootstrap — shared test setup.
 *
 * Exposes ``kayak_test_pdo()``: returns a fresh in-memory PDO with just
 * enough schema to exercise auth / magic-link / session code. Starter
 * tests inline the CREATE TABLE they need; this helper saves the repeat.
 *
 * The EDITOR_SESSION_COOKIE / EDITOR_CSRF_COOKIE / EDITOR_SESSION_DAYS
 * constants are defined by php/includes/auth.php. Every test that uses
 * them also requires auth.php, so they are always present when needed —
 * no bootstrap-side pre-definitions (those collide with auth.php's
 * `const` declarations under PHP 9).
 */

/** Fresh in-memory SQLite PDO with a minimal editor/magic-link schema. */
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
    ");
    return $pdo;
}
