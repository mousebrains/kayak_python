<?php
/**
 * Shared MySQL/SQLite database connection helper.
 *
 * Reads DATABASE_URL from environment (.env or server config).
 * Falls back to SQLite at the project root.
 */

function get_db(): PDO {
    static $pdo = null;
    if ($pdo !== null) return $pdo;

    $url = getenv('DATABASE_URL') ?: '';

    if (str_starts_with($url, 'mysql')) {
        // mysql://user:pass@host/dbname or mysql+pymysql://...
        $parts = parse_url(preg_replace('/^mysql\+\w+:/', 'mysql:', $url));
        $host = $parts['host'] ?? 'localhost';
        $port = $parts['port'] ?? 3306;
        $db   = ltrim($parts['path'] ?? '/kayak', '/');
        $user = $parts['user'] ?? 'root';
        $pass = $parts['pass'] ?? '';
        $dsn  = "mysql:host=$host;port=$port;dbname=$db;charset=utf8mb4";
        $pdo  = new PDO($dsn, $user, $pass, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
    } else {
        // SQLite — look for kayak.db relative to project root
        $db_path = getenv('SQLITE_PATH') ?: dirname(__DIR__, 2) . '/kayak.db';
        $pdo = new PDO("sqlite:$db_path", null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
        $pdo->exec('PRAGMA journal_mode=WAL');
        $pdo->exec('PRAGMA foreign_keys=ON');
    }
    return $pdo;
}
