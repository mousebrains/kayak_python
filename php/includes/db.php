<?php
declare(strict_types=1);
/**
 * SQLite database connection helper.
 *
 * Reads SQLITE_PATH from environment, falling back to ../DB/kayak.db relative to the project root.
 */

function get_db(): PDO {
    static $pdo = null;
    if ($pdo !== null) return $pdo;

    $db_path = getenv('SQLITE_PATH') ?: dirname(__DIR__, 2) . '/../DB/kayak.db';
    try {
        $pdo = new PDO("sqlite:$db_path", null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
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
    if (!$reach) { http_response_code(404); exit('Reach not found'); }
    return $reach;
}
