<?php
declare(strict_types=1);
/**
 * Latest values API.
 *
 * Usage: /latest.php?id=<reach_id>
 * Returns JSON with latest value for each data type.
 */
require_once __DIR__ . '/includes/db.php';

header('Content-Type: application/json');
header('Cache-Control: max-age=60');
// Public read-only API: river-level data is non-sensitive and we want
// kayakers' apps and integrations (river forecasting, club sites) to
// pull it freely. The wildcard is intentional.
header('Access-Control-Allow-Origin: *');

$id = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT);
if (!is_int($id) || $id < 1) { http_response_code(400); echo json_encode(['error' => 'Missing id']); exit; }

$db = get_db();

$stmt = $db->prepare('SELECT gauge_id, name, display_name FROM reach WHERE id = ?');
$stmt->execute([$id]);
$reach = $stmt->fetch();
if ($reach === false) { http_response_code(404); echo json_encode(['error' => 'Not found']); exit; }

$name = ($reach['display_name'] ?? '') !== '' ? $reach['display_name'] : $reach['name'];
$types = [];

if ($reach['gauge_id'] !== null) {
    $stmt = $db->prepare(
        'SELECT data_type, value, observed_at, delta_per_hour, prev_value, prev_observed_at
         FROM latest_gauge_observation WHERE gauge_id = ?'
    );
    $stmt->execute([$reach['gauge_id']]);
    foreach ($stmt->fetchAll() as $r) {
        $types[$r['data_type']] = [
            'value'          => (float)$r['value'],
            'time'           => $r['observed_at'] ? date('c', strtotime($r['observed_at'])) : null,
            'delta_per_hour' => $r['delta_per_hour'] !== null ? (float)$r['delta_per_hour'] : null,
            'prev_value'     => $r['prev_value'] !== null ? (float)$r['prev_value'] : null,
            'prev_time'      => $r['prev_observed_at'] ? date('c', strtotime($r['prev_observed_at'])) : null,
        ];
    }
}

echo json_encode([
    'reach' => $reach['name'],
    'name'  => $name,
    'types' => $types,
]);
