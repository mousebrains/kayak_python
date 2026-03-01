<?php
/**
 * SQLite database connection helper.
 *
 * Reads SQLITE_PATH from environment, falling back to kayak.db at the project root.
 */

function get_db(): PDO {
    static $pdo = null;
    if ($pdo !== null) return $pdo;

    $db_path = getenv('SQLITE_PATH') ?: dirname(__DIR__, 2) . '/kayak.db';
    $pdo = new PDO("sqlite:$db_path", null, null, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
    $pdo->exec('PRAGMA journal_mode=WAL');
    $pdo->exec('PRAGMA foreign_keys=ON');
    $pdo->exec('PRAGMA busy_timeout=5000');
    return $pdo;
}
