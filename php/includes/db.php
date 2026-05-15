<?php
declare(strict_types=1);
/**
 * SQLite database connection helper.
 *
 * Resolves the database path via the typed config (`Config::str('database_path')`,
 * sourced from /etc/kayak/runtime-config.json), falling back to the SQLITE_PATH
 * env var, then to a path computed relative to __DIR__ for both layouts:
 *   dev:  /Users/.../kayak/php/includes/db.php   -> /Users/.../DB/kayak.db
 *   prod: /home/pat/public_html/includes/db.php  -> /home/pat/DB/kayak.db
 * dirname(__DIR__, 3) walks up includes/ -> (php|public_html)/ -> project-parent,
 * and joins in /DB/kayak.db — the same sibling layout used by backup/decimate.
 */

require_once __DIR__ . '/config.php';

/**
 * Resolve the SQLite path without opening a connection, so it's unit-testable.
 */
function _sqlite_path(): string {
    $cfg = Config::str('database_path');
    if ($cfg !== '') return $cfg;
    $env = getenv('SQLITE_PATH');
    if ($env !== false && $env !== '') return $env;
    return dirname(__DIR__, 3) . '/DB/kayak.db';
}

function get_db(): PDO {
    static $pdo = null;
    if ($pdo !== null) return $pdo;

    $db_path = _sqlite_path();
    try {
        $pdo = new PDO("sqlite:$db_path", null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            // SQLite PDO uses native prepares regardless, but pinning this off
            // defends against a future driver swap (e.g. MySQL/Postgres) where
            // emulated prepares are on by default and silently lose type info.
            PDO::ATTR_EMULATE_PREPARES => false,
        ]);
        $pdo->exec('PRAGMA journal_mode=WAL');
        $pdo->exec('PRAGMA foreign_keys=ON');
        $pdo->exec('PRAGMA busy_timeout=30000');
        $pdo->exec('PRAGMA synchronous=NORMAL');
    } catch (\PDOException $e) {
        error_log('Database connection failed: ' . $e->getMessage());
        http_response_code(503);
        exit('Service temporarily unavailable');
    }
    return $pdo;
}

/**
 * Fetch a reach by ID, or exit 404 if not found.
 *
 * @return array<string, mixed> The reach row.
 */
function get_reach_or_404(int $id): array {
    $stmt = get_db()->prepare('SELECT * FROM reach WHERE id = ?');
    $stmt->execute([$id]);
    $reach = $stmt->fetch();
    if (!$reach) {
        require_once __DIR__ . '/error.php';
        render_error_page(
            404,
            'Reach not found',
            '<p>No reach with id ' . (int)$id . ' exists. It may have been removed or merged.</p>'
        );
    }
    return $reach;
}
