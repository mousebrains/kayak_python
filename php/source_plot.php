<?php
declare(strict_types=1);
/**
 * Per-source SVG time-series plot.
 *
 * Usage:  /source_plot.php?id=<source_id>&type=<data_type>[&days=10]
 *                                                          [&start=YYYY-MM-DD&end=YYYY-MM-DD]
 *                                                          [&embed=1]
 *
 * Mirrors plot.php's shape but scopes the data to a single source +
 * data_type instead of joining through reach → gauge → gauge_source.
 * Used by source.php's Observations table to give the operator a
 * one-click view of a single source's history.
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/svg_plot.php';
require_once __DIR__ . '/includes/validate.php';

$id   = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT);
$type = filter_input(INPUT_GET, 'type', FILTER_SANITIZE_SPECIAL_CHARS) ?: 'flow';
$days = filter_input(INPUT_GET, 'days', FILTER_VALIDATE_INT) ?: 10;
// Clamp the lookback to 1 year — same rationale as plot.php's cap.
$days = max(1, min($days, 366));
$embed = filter_input(INPUT_GET, 'embed', FILTER_VALIDATE_INT);
// filter_input returns string|false|null; validate_date wants string|null.
$start_raw  = filter_input(INPUT_GET, 'start', FILTER_SANITIZE_SPECIAL_CHARS);
$end_raw    = filter_input(INPUT_GET, 'end', FILTER_SANITIZE_SPECIAL_CHARS);
$start_date = validate_date(is_string($start_raw) ? $start_raw : null);
$end_date   = validate_date(is_string($end_raw)   ? $end_raw   : null);

if (!$id) { http_response_code(400); exit('Missing id parameter'); }

// Aliases match plot.php so /source_plot.php?type=gage and ?type=temp work.
if ($type === 'gage') { $type = 'gauge'; }
if ($type === 'temp') { $type = 'temperature'; }
$valid_types = ['flow', 'gauge', 'temperature', 'inflow', 'outflow'];
if (!in_array($type, $valid_types, true)) {
    http_response_code(400); exit('Invalid type');
}

$db = get_db();

$stmt = $db->prepare('SELECT id, name, agency FROM source WHERE id = ?');
$stmt->execute([$id]);
$source = $stmt->fetch();
if (!$source) { http_response_code(404); exit('Source not found'); }

if ($start_date && $end_date) {
    // validate_date guarantees a parseable Y-m-d, but strtotime is typed
    // int|false — cast to int to satisfy PHPStan without a baseline entry.
    $start_ts = (int)strtotime($start_date);
    $end_ts   = (int)strtotime($end_date);
    if ($end_ts - $start_ts > 366 * 86400) {
        $start_ts = $end_ts - 366 * 86400;
        $start_date = date('Y-m-d', $start_ts);
    }
    $since = date('Y-m-d 00:00:00', $start_ts);
    $until = date('Y-m-d 23:59:59', $end_ts);
    $stmt = $db->prepare(
        'SELECT observed_at, value FROM observation
         WHERE source_id = ? AND data_type = ? AND observed_at >= ? AND observed_at <= ?
         ORDER BY observed_at
         LIMIT 100000'
    );
    $stmt->execute([$id, $type, $since, $until]);
} else {
    $since = date('Y-m-d H:i:s', time() - $days * 86400);
    $stmt = $db->prepare(
        'SELECT observed_at, value FROM observation
         WHERE source_id = ? AND data_type = ? AND observed_at >= ?
         ORDER BY observed_at
         LIMIT 100000'
    );
    $stmt->execute([$id, $type, $since]);
}
$rows = $stmt->fetchAll();

$times = [];
$values = [];
foreach ($rows as $r) {
    $times[]  = strtotime($r['observed_at']);
    $values[] = (float)$r['value'];
}

$labels = [
    'flow'        => 'Flow (CFS)',
    'gauge'       => 'Gage Height (Ft)',
    'temperature' => 'Temperature (F)',
    'inflow'      => 'Inflow (CFS)',
    'outflow'     => 'Outflow (CFS)',
];
// $type is constrained to $valid_types above, which matches $labels' keys
// exactly — so the offset always exists; no fallback needed.
$y_label  = $labels[$type];
$src_name = (string)$source['name'];
$title    = "$src_name — $y_label";
$is_flow  = in_array($type, ['flow', 'inflow', 'outflow'], true);

$svg = generate_svg_plot($times, $values, $title, $y_label, 800, 350, 200, $is_flow);

if ($embed) {
    $latest_ts     = count($times) > 0 ? max($times) : time();
    $default_end   = date('Y-m-d', $latest_ts);
    $default_start = date('Y-m-d', $latest_ts - $days * 86400);
    $form_start    = $start_date ?: $default_start;
    $form_end      = $end_date   ?: $default_end;

    require_once __DIR__ . '/includes/header.php';
    require_once __DIR__ . '/includes/footer.php';
    header('Cache-Control: max-age=300');
    include_header("$src_name - $y_label", '', '', '', ['picker_kind' => 'gauge']);
    echo '<h2>' . htmlspecialchars($src_name) . ' — ' . htmlspecialchars($y_label) . '</h2>';
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
    echo '<a href="/source.php?id=' . $id . '">Back to source</a>';
    echo ' | <a href="/source_data.php?id=' . $id . '">Raw data</a></p>';
    include_footer();
} else {
    header('Content-Type: image/svg+xml');
    header('Cache-Control: max-age=300');
    echo $svg;
}
