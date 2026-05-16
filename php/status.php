<?php
declare(strict_types=1);
/**
 * /status.json — public read-only health snapshot.
 *
 * Audience: the public status page (status.mousebrains.com), the
 * Tier 2.4 internal dashboard (levels.mousebrains.com/_internal/),
 * and any monitor / script that wants a one-shot answer to
 * "is the data flowing?"
 *
 * Per docs/PLAN_production_discipline.md Phase 2.1. Shape settled
 * in chat 2026-05-15:
 *
 *   {
 *     "build_at": "2026-05-16T00:12:00Z",
 *     "latest_observation_at": "2026-05-16T00:08:00Z",
 *     "stale_threshold_hours": 48,
 *     "sources_by_agency": [
 *       {"agency": "USGS", "fresh": 142, "stale": 3, "expired": 0,
 *        "latest_observation_at": "..."},
 *       ...
 *     ],
 *     "gauges_by_status": {"low": 94, "okay": 73, "high": 4, "unknown": 21},
 *     "totals": {"sources": 290, "active_sources": 283,
 *                "gauges_with_status": 192}
 *   }
 */
require_once __DIR__ . '/includes/db.php';

header('Content-Type: application/json');
header('Cache-Control: no-cache, max-age=10');

// CORS allow-list (status.mousebrains.com is the hosted status page;
// levels.mousebrains.com is the future internal dashboard host —
// both can fetch /status.json cross-origin).
$allowed_origins = [
    'https://status.mousebrains.com',
    'https://levels.mousebrains.com',
];
$origin = $_SERVER['HTTP_ORIGIN'] ?? '';
if (in_array($origin, $allowed_origins, true)) {
    header('Access-Control-Allow-Origin: ' . $origin);
    header('Vary: Origin');
}

// Mirrors src/kayak/web/build/_shared.py:20 (DATA_STALE_THRESHOLD).
// Hard-coded rather than read from a config file — the cross-language
// share isn't worth the plumbing at this scale.
const STALE_THRESHOLD_HOURS = 48;
const EXPIRED_THRESHOLD_DAYS = 7;

/**
 * Narrow PDO::query()'s PDOStatement|false return for PHPStan level 8.
 * ERRMODE_EXCEPTION (set in db.php) means the false branch is
 * unreachable at runtime, but the static type still includes it.
 */
function status_query(PDO $db, string $sql): PDOStatement {
    $stmt = $db->query($sql);
    if ($stmt === false) {
        throw new RuntimeException('PDO query returned false: ' . $sql);
    }
    return $stmt;
}

$db = get_db();

// build_at — mtime of index.html, the build's authoritative landing page.
// The build writes index.html last, so its mtime tracks "build finished".
$index_path = __DIR__ . '/index.html';
$build_mtime = file_exists($index_path) ? filemtime($index_path) : false;
$build_at = $build_mtime !== false ? gmdate('Y-m-d\TH:i:s\Z', $build_mtime) : null;

// latest_observation_at — most recent observation across all sources.
$row = status_query($db, 'SELECT MAX(observed_at) AS m FROM latest_observation')->fetch();
$latest_observation_at = is_array($row) && $row['m']
    ? gmdate('Y-m-d\TH:i:s\Z', (int)strtotime((string)$row['m']))
    : null;

// sources_by_agency — bucket each source by its freshest observation,
// then count by agency. Sources with no observations don't count.
$stmt = status_query($db,
    'WITH source_freshness AS (
        SELECT s.id, s.agency, MAX(lo.observed_at) AS latest_at
        FROM source s
        LEFT JOIN latest_observation lo ON lo.source_id = s.id
        GROUP BY s.id, s.agency
    )
    SELECT
        agency,
        SUM(CASE WHEN latest_at >= datetime("now", "-' . STALE_THRESHOLD_HOURS . ' hours") THEN 1 ELSE 0 END) AS fresh,
        SUM(CASE WHEN latest_at <  datetime("now", "-' . STALE_THRESHOLD_HOURS . ' hours")
                  AND latest_at >= datetime("now", "-' . EXPIRED_THRESHOLD_DAYS . ' days") THEN 1 ELSE 0 END) AS stale,
        SUM(CASE WHEN latest_at <  datetime("now", "-' . EXPIRED_THRESHOLD_DAYS . ' days") THEN 1 ELSE 0 END) AS expired,
        MAX(latest_at) AS latest_at
    FROM source_freshness
    WHERE agency IS NOT NULL AND latest_at IS NOT NULL
    GROUP BY agency
    ORDER BY (fresh + stale + expired) DESC, agency'
);
$sources_by_agency = [];
foreach ($stmt->fetchAll() as $r) {
    $sources_by_agency[] = [
        'agency'                => (string)$r['agency'],
        'fresh'                 => (int)$r['fresh'],
        'stale'                 => (int)$r['stale'],
        'expired'               => (int)$r['expired'],
        'latest_observation_at' => $r['latest_at']
            ? gmdate('Y-m-d\TH:i:s\Z', (int)strtotime((string)$r['latest_at']))
            : null,
    ];
}

// gauges_by_status — read the deployed gauges-state.json rather than
// recomputing reach-status math in PHP. The build wrote it minutes
// ago; the structure is {gauge_id: {s: "low|okay|high|unknown", ...}}
// plus a _meta key the loop skips.
$gauges_path = __DIR__ . '/static/gauges-state.json';
$gauges_by_status = ['low' => 0, 'okay' => 0, 'high' => 0, 'unknown' => 0];
$gauges_with_status = 0;
if (file_exists($gauges_path)) {
    $raw = file_get_contents($gauges_path);
    $decoded = $raw !== false ? json_decode($raw, true) : null;
    if (is_array($decoded)) {
        foreach ($decoded as $gid => $entry) {
            if ($gid === '_meta' || !is_array($entry)) {
                continue;
            }
            $s = is_string($entry['s'] ?? null) ? $entry['s'] : 'unknown';
            if (!isset($gauges_by_status[$s])) {
                $s = 'unknown';
            }
            $gauges_by_status[$s]++;
            $gauges_with_status++;
        }
    }
}

// Totals — useful at-a-glance + sanity-check the buckets sum.
$total_sources  = (int)status_query($db, 'SELECT COUNT(*) FROM source')->fetchColumn();
$active_sources = (int)status_query($db, 'SELECT COUNT(DISTINCT source_id) FROM latest_observation')->fetchColumn();

echo json_encode([
    'build_at'              => $build_at,
    'latest_observation_at' => $latest_observation_at,
    'stale_threshold_hours' => STALE_THRESHOLD_HOURS,
    'sources_by_agency'     => $sources_by_agency,
    'gauges_by_status'      => $gauges_by_status,
    'totals'                => [
        'sources'            => $total_sources,
        'active_sources'     => $active_sources,
        'gauges_with_status' => $gauges_with_status,
    ],
], JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT);
