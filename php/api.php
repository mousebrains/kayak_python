<?php
/**
 * JSON data API.
 *
 * Usage: /api.php?id=<section_id>&type=<data_type>[&days=60][&points=200][&format=verbose]
 *
 * Default compact format: {"ts": [epoch, ...], "v": [value, ...]}
 * Verbose format:         {"data": [{"time": "ISO", "value": 1.0}, ...]}
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/lttb.php';

header('Content-Type: application/json');
header('Cache-Control: max-age=300');
header('Access-Control-Allow-Origin: *');

$id     = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT);
$type   = filter_input(INPUT_GET, 'type', FILTER_SANITIZE_SPECIAL_CHARS) ?: 'flow';
$days   = filter_input(INPUT_GET, 'days', FILTER_VALIDATE_INT) ?: 60;
$points = filter_input(INPUT_GET, 'points', FILTER_VALIDATE_INT) ?: 0;
$format = filter_input(INPUT_GET, 'format', FILTER_SANITIZE_SPECIAL_CHARS) ?: 'compact';

if (!$id) { http_response_code(400); echo json_encode(['error' => 'Missing id']); exit; }

// Normalize type aliases
if ($type === 'gage') $type = 'gauge';
if ($type === 'temp') $type = 'temperature';

$db = get_db();

$stmt = $db->prepare('SELECT gauge_id, name FROM section WHERE id = ?');
$stmt->execute([$id]);
$section = $stmt->fetch();
if (!$section || !$section['gauge_id']) {
    echo json_encode(['section' => $section['name'] ?? '', 'type' => $type, 'count' => 0, 'ts' => [], 'v' => []]);
    exit;
}

$stmt = $db->prepare('SELECT source_id FROM gauge_source WHERE gauge_id = ? LIMIT 1');
$stmt->execute([$section['gauge_id']]);
$gs = $stmt->fetch();
if (!$gs) {
    echo json_encode(['section' => $section['name'], 'type' => $type, 'count' => 0, 'ts' => [], 'v' => []]);
    exit;
}

$since = date('Y-m-d H:i:s', time() - $days * 86400);

$stmt = $db->prepare(
    'SELECT observed_at, value FROM observation
     WHERE source_id = ? AND data_type = ? AND observed_at >= ?
     ORDER BY observed_at'
);
$stmt->execute([$gs['source_id'], $type, $since]);
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
        'section' => $section['name'],
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
        'section' => $section['name'],
        'type'    => $type,
        'count'   => count($ts),
        'ts'      => $ts,
        'v'       => $v,
    ]);
}
