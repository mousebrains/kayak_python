<?php
declare(strict_types=1);
/**
 * JSON data API.
 *
 * Usage: /api.php?id=<reach_id>&type=<data_type>[&days=60][&points=200][&format=verbose]
 *
 * Default compact format: {"ts": [epoch, ...], "v": [value, ...]}
 * Verbose format:         {"data": [{"time": "ISO", "value": 1.0}, ...]}
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/lttb.php';

header('Content-Type: application/json');
header('Cache-Control: max-age=300');
// Public read-only API: river-level data is non-sensitive and we want
// kayakers' apps and integrations (river forecasting, club sites) to
// pull it freely. The wildcard is intentional.
header('Access-Control-Allow-Origin: *');

$id     = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT);
$type   = filter_input(INPUT_GET, 'type', FILTER_SANITIZE_SPECIAL_CHARS);
$type   = is_string($type) && $type !== '' ? $type : 'flow';
$days   = filter_input(INPUT_GET, 'days', FILTER_VALIDATE_INT);
$days   = is_int($days) && $days !== 0 ? $days : 60;
$days   = min($days, 365);
$points = filter_input(INPUT_GET, 'points', FILTER_VALIDATE_INT);
$points = is_int($points) && $points !== 0 ? $points : 0;
$format = filter_input(INPUT_GET, 'format', FILTER_SANITIZE_SPECIAL_CHARS);
$format = is_string($format) && $format !== '' ? $format : 'compact';

if (!is_int($id) || $id < 1) { http_response_code(400); echo json_encode(['error' => 'Missing id']); exit; }

// Normalize type aliases
if ($type === 'gage') $type = 'gauge';
if ($type === 'temp') $type = 'temperature';

$db = get_db();

$stmt = $db->prepare('SELECT gauge_id, name FROM reach WHERE id = ?');
$stmt->execute([$id]);
$reach = $stmt->fetch();
if ($reach === false || $reach['gauge_id'] === null) {
    echo json_encode(['reach' => $reach['name'] ?? '', 'type' => $type, 'count' => 0, 'ts' => [], 'v' => []]);
    exit;
}

$since = date('Y-m-d H:i:s', time() - $days * 86400);

$stmt = $db->prepare(
    'SELECT o.observed_at, o.value FROM observation o
     JOIN gauge_source gs ON o.source_id = gs.source_id
     WHERE gs.gauge_id = ? AND o.data_type = ? AND o.observed_at >= ?
     ORDER BY o.observed_at'
);
$stmt->execute([$reach['gauge_id'], $type, $since]);
$rows = $stmt->fetchAll();

// Optional LTTB downsampling
if ($points > 0 && count($rows) > $points) {
    $pairs = [];
    foreach ($rows as $r) {
        $pairs[] = [(float)strtotime($r['observed_at']), (float)$r['value']];
    }
    $pairs = lttb_downsample($pairs, $points);
    // Rebuild rows from downsampled pairs
    $rows = [];
    foreach ($pairs as [$ts, $val]) {
        $rows[] = ['observed_at' => date('Y-m-d H:i:s', (int)$ts), 'value' => $val];
    }
}

if ($format === 'verbose') {
    $data = [];
    foreach ($rows as $r) {
        $data[] = [
            'time'  => date('c', strtotime($r['observed_at'])),
            'value' => (float)$r['value'],
        ];
    }
    echo json_encode([
        'reach' => $reach['name'],
        'type'    => $type,
        'count'   => count($data),
        'data'    => $data,
    ]);
} else {
    // Compact: parallel arrays with epoch timestamps
    $ts = []; $v = [];
    foreach ($rows as $r) {
        $ts[] = strtotime($r['observed_at']);
        $v[]  = (float)$r['value'];
    }
    echo json_encode([
        'reach' => $reach['name'],
        'type'    => $type,
        'count'   => count($ts),
        'ts'      => $ts,
        'v'       => $v,
    ]);
}
