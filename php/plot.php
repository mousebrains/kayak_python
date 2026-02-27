<?php
/**
 * Time-series SVG plot.
 *
 * Usage: /plot.php?type=flow&id=<section_id>[&days=60]
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/svg_plot.php';

$id   = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT);
$type = filter_input(INPUT_GET, 'type', FILTER_SANITIZE_SPECIAL_CHARS) ?: 'flow';
$days = filter_input(INPUT_GET, 'days', FILTER_VALIDATE_INT) ?: 60;
$embed = filter_input(INPUT_GET, 'embed', FILTER_VALIDATE_INT);

if (!$id) { http_response_code(400); exit('Missing id parameter'); }

$valid_types = ['flow', 'gauge', 'gage', 'temperature', 'temp', 'inflow', 'outflow'];
if (!in_array($type, $valid_types)) {
    http_response_code(400); exit('Invalid type');
}

// Normalize aliases
if ($type === 'gage') $type = 'gauge';
if ($type === 'temp') $type = 'temperature';

$db = get_db();

// Look up section → gauge → source
$stmt = $db->prepare('SELECT gauge_id, display_name, name FROM section WHERE id = ?');
$stmt->execute([$id]);
$section = $stmt->fetch();
if (!$section || !$section['gauge_id']) { http_response_code(404); exit('Not found'); }

$name = $section['display_name'] ?: $section['name'];

$stmt = $db->prepare('SELECT source_id FROM gauge_source WHERE gauge_id = ? LIMIT 1');
$stmt->execute([$section['gauge_id']]);
$gs = $stmt->fetch();
if (!$gs) { http_response_code(404); exit('No source'); }

$source_id = $gs['source_id'];
$since = date('Y-m-d H:i:s', time() - $days * 86400);

$stmt = $db->prepare(
    'SELECT observed_at, value FROM observation
     WHERE source_id = ? AND data_type = ? AND observed_at >= ?
     ORDER BY observed_at'
);
$stmt->execute([$source_id, $type, $since]);
$rows = $stmt->fetchAll();

$times = []; $values = [];
foreach ($rows as $r) {
    $times[]  = strtotime($r['observed_at']);
    $values[] = (float)$r['value'];
}

$labels = [
    'flow' => 'Flow (CFS)',
    'gauge' => 'Gage Height (Ft)',
    'temperature' => 'Temperature (F)',
    'inflow' => 'Inflow (CFS)',
    'outflow' => 'Outflow (CFS)',
];
$y_label = $labels[$type] ?? $type;
$title = "$name — $y_label";

$svg = generate_svg_plot($times, $values, $title, $y_label);

if ($embed) {
    // Serve as HTML page with the SVG embedded
    require_once __DIR__ . '/includes/header.php';
    require_once __DIR__ . '/includes/footer.php';
    header('Cache-Control: max-age=300');
    include_header("$name - $y_label");
    echo '<h2>' . htmlspecialchars($name) . '</h2>';
    echo '<div class="plot-container">' . $svg . '</div>';
    echo '<p style="margin-top:.5rem;font-size:.85rem">';
    echo '<a href="/description.php?id=' . $id . '">Description</a>';
    echo ' | <a href="/api.php?type=' . $type . '&id=' . $id . '&days=' . $days . '">JSON data</a>';
    echo ' | <a href="/index.html">Back</a></p>';
    include_footer();
} else {
    // Serve raw SVG
    header('Content-Type: image/svg+xml');
    header('Cache-Control: max-age=300');
    echo $svg;
}
