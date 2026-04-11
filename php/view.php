<?php
declare(strict_types=1);
/**
 * Raw data view — shows latest readings for a reach.
 *
 * Usage: /view.php?id=<reach_id>
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

$id = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT);
if (!$id) { http_response_code(400); exit('Missing id parameter'); }

$db = get_db();
$reach = get_reach_or_404($id);

$name = $reach['display_name'] ?: $reach['name'];

header('Cache-Control: max-age=60');
include_header("$name - Data");

echo '<h2>' . htmlspecialchars($name) . '</h2>';

if (!$reach['gauge_id']) {
    echo '<p>No gauge data available.</p>';
    echo '<p><a href="/index.html">Back</a></p>';
    include_footer();
    exit;
}

$stmt = $db->prepare(
    'SELECT data_type, value, observed_at, delta_per_hour
     FROM latest_gauge_observation WHERE gauge_id = ?'
);
$stmt->execute([$reach['gauge_id']]);
$rows = $stmt->fetchAll();

if ($rows) {
    echo '<table class="view-table">';
    echo '<tr><th>Type</th><th>Value</th><th>Time</th><th>Change/hr</th></tr>';
    foreach ($rows as $r) {
        $dtype = htmlspecialchars($r['data_type']);
        $val   = number_format((float)$r['value'], 2);
        $time  = $r['observed_at'] ? date('Y-m-d H:i', strtotime($r['observed_at'])) : 'N/A';
        $delta = $r['delta_per_hour'] !== null ? number_format((float)$r['delta_per_hour'], 2) : 'N/A';
        echo "<tr><td>$dtype</td><td>$val</td><td>$time</td><td>$delta</td></tr>\n";
    }
    echo '</table>';
} else {
    echo '<p>No recent data.</p>';
}

echo '<p style="margin-top:1rem">';
echo '<a href="/description.php?id=' . $id . '">Description</a>';
echo ' | <a href="/plot.php?type=flow&id=' . $id . '&embed=1">Flow plot</a>';
echo ' | <a href="/index.html">Back</a></p>';

include_footer();
