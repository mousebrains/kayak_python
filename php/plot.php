<?php
/**
 * Time-series SVG plot.
 *
 * Usage: /plot.php?type=flow&id=<reach_id>[&days=60]
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/svg_plot.php';
require_once __DIR__ . '/includes/validate.php';

$id   = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT);
$type = filter_input(INPUT_GET, 'type', FILTER_SANITIZE_SPECIAL_CHARS) ?: 'flow';
$days = filter_input(INPUT_GET, 'days', FILTER_VALIDATE_INT) ?: 10;
$embed = filter_input(INPUT_GET, 'embed', FILTER_VALIDATE_INT);
$start_date = validate_date(filter_input(INPUT_GET, 'start', FILTER_SANITIZE_SPECIAL_CHARS));
$end_date = validate_date(filter_input(INPUT_GET, 'end', FILTER_SANITIZE_SPECIAL_CHARS));

if (!$id) { http_response_code(400); exit('Missing id parameter'); }

$valid_types = ['flow', 'gauge', 'gage', 'temperature', 'temp', 'inflow', 'outflow', 'dual'];
if (!in_array($type, $valid_types)) {
    http_response_code(400); exit('Invalid type');
}

// Normalize aliases
if ($type === 'gage') $type = 'gauge';
if ($type === 'temp') $type = 'temperature';

$db = get_db();

// Look up reach → gauge → source
$reach = get_reach_or_404($id);
if (!$reach['gauge_id']) { http_response_code(404); exit('No gauge for this reach'); }

$name = $reach['display_name'] ?: $reach['name'];

$gauge_id = $reach['gauge_id'];
$is_flow = in_array($type, ['flow', 'inflow', 'outflow']);

if ($start_date && $end_date) {
    $since = date('Y-m-d 00:00:00', strtotime($start_date));
    $until = date('Y-m-d 23:59:59', strtotime($end_date));
    $stmt = $db->prepare(
        'SELECT o.observed_at, o.value FROM observation o
         JOIN gauge_source gs ON o.source_id = gs.source_id
         WHERE gs.gauge_id = ? AND o.data_type = ? AND o.observed_at >= ? AND o.observed_at <= ?
         ORDER BY o.observed_at'
    );
    $stmt->execute([$gauge_id, $type, $since, $until]);
} else {
    $since = date('Y-m-d H:i:s', time() - $days * 86400);
    $stmt = $db->prepare(
        'SELECT o.observed_at, o.value FROM observation o
         JOIN gauge_source gs ON o.source_id = gs.source_id
         WHERE gs.gauge_id = ? AND o.data_type = ? AND o.observed_at >= ?
         ORDER BY o.observed_at'
    );
    $stmt->execute([$gauge_id, $type, $since]);
}
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

$svg = generate_svg_plot($times, $values, $title, $y_label, 800, 350, 200, $is_flow);

if ($embed) {
    // Compute default date range from latest data point
    $latest_ts = count($times) > 0 ? max($times) : time();
    $default_end = date('Y-m-d', $latest_ts);
    $default_start = date('Y-m-d', $latest_ts - 10 * 86400);
    $form_start = $start_date ?: $default_start;
    $form_end = $end_date ?: $default_end;

    // Serve as HTML page with the SVG embedded
    require_once __DIR__ . '/includes/header.php';
    require_once __DIR__ . '/includes/footer.php';
    header('Cache-Control: max-age=300');
    include_header("$name - $y_label");
    echo '<h2>' . htmlspecialchars($name) . '</h2>';
    echo '<form method="get" style="margin-bottom:.5rem;font-size:.85rem">';
    echo '<input type="hidden" name="id" value="' . $id . '">';
    echo '<input type="hidden" name="type" value="' . htmlspecialchars($type) . '">';
    echo '<input type="hidden" name="embed" value="1">';
    echo '<label>Start: <input type="date" name="start" value="' . htmlspecialchars($form_start) . '"></label> ';
    echo '<label>End: <input type="date" name="end" value="' . htmlspecialchars($form_end) . '"></label> ';
    echo '<button type="submit">Update</button>';
    echo '</form>';
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
