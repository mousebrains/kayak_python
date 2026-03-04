<?php
/**
 * Data inspector — raw observation data for a reach's gauge sources.
 *
 * Usage: /data.php?id=<reach_id>[&start=YYYY-MM-DD&end=YYYY-MM-DD]
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

$id = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT);
$start_date = filter_input(INPUT_GET, 'start', FILTER_SANITIZE_SPECIAL_CHARS);
$end_date = filter_input(INPUT_GET, 'end', FILTER_SANITIZE_SPECIAL_CHARS);

if (!$id) { http_response_code(400); exit('Missing id parameter'); }

$db = get_db();

$reach = $db->prepare('SELECT * FROM reach WHERE id = ?');
$reach->execute([$id]);
$reach = $reach->fetch();
if (!$reach) { http_response_code(404); exit('Reach not found'); }

$name = $reach['display_name'] ?: $reach['name'];

// Find all sources via gauge
$source_ids = [];
$source_map = []; // source_id => {name, agency, letter}
if ($reach['gauge_id']) {
    $stmt = $db->prepare(
        'SELECT s.id, s.name, s.agency
         FROM source s
         JOIN gauge_source gs ON gs.source_id = s.id
         WHERE gs.gauge_id = ?
         ORDER BY s.id'
    );
    $stmt->execute([$reach['gauge_id']]);
    $sources = $stmt->fetchAll();
    $letter = 'A';
    foreach ($sources as $s) {
        $source_ids[] = $s['id'];
        $source_map[$s['id']] = [
            'name' => $s['name'],
            'agency' => $s['agency'],
            'letter' => $letter++,
        ];
    }
}

if (!$source_ids) {
    header('Cache-Control: no-cache');
    include_header("$name - Data");
    echo '<h2>' . htmlspecialchars($name) . ' — Data Inspector</h2>';
    echo '<p>No sources linked to this reach.</p>';
    echo '<p><a href="/description.php?id=' . $id . '">Back to description</a></p>';
    include_footer();
    exit;
}

// Default date range: last 2 days
$default_end = date('Y-m-d');
$default_start = date('Y-m-d', time() - 2 * 86400);
$form_start = $start_date ?: $default_start;
$form_end = $end_date ?: $default_end;
$since = date('Y-m-d 00:00:00', strtotime($form_start));
$until = date('Y-m-d 23:59:59', strtotime($form_end));

// Query observations for all sources in range
$placeholders = implode(',', array_fill(0, count($source_ids), '?'));
$params = array_merge($source_ids, [$since, $until]);
$stmt = $db->prepare(
    "SELECT source_id, data_type, value, observed_at FROM observation
     WHERE source_id IN ($placeholders) AND observed_at >= ? AND observed_at <= ?
     ORDER BY observed_at DESC
     LIMIT 10000"
);
$stmt->execute($params);
$rows = $stmt->fetchAll();

// Pivot: collect all data types and build rows keyed by (observed_at, source_id)
$data_types = [];
$pivoted = []; // "observed_at|source_id" => [data_type => value]
foreach ($rows as $r) {
    $key = $r['observed_at'] . '|' . $r['source_id'];
    $data_types[$r['data_type']] = true;
    if (!isset($pivoted[$key])) {
        $pivoted[$key] = [
            'observed_at' => $r['observed_at'],
            'source_id' => $r['source_id'],
        ];
    }
    $pivoted[$key][$r['data_type']] = $r['value'];
}
$data_types = array_keys($data_types);
sort($data_types);

$type_labels = [
    'flow' => 'Flow',
    'gauge' => 'Gage Ht',
    'temperature' => 'Temp',
    'inflow' => 'Inflow',
    'outflow' => 'Outflow',
];

header('Cache-Control: no-cache');
include_header(htmlspecialchars($name) . ' - Data');

echo '<h2>' . htmlspecialchars($name) . ' — Data Inspector</h2>';

// Date form
echo '<form method="get" style="margin:.5rem 0;font-size:.85rem">';
echo '<input type="hidden" name="id" value="' . $id . '">';
echo '<label>Start: <input type="date" name="start" value="' . htmlspecialchars($form_start) . '"></label> ';
echo '<label>End: <input type="date" name="end" value="' . htmlspecialchars($form_end) . '"></label> ';
echo '<button type="submit">Update</button>';
echo '</form>';

if (!$pivoted) {
    echo '<p>No observations in this date range.</p>';
} else {
    $show_src = count($source_ids) > 1;

    echo '<table class="readings-table">';
    echo '<tr><th>Time</th>';
    if ($show_src) echo '<th>Src</th>';
    foreach ($data_types as $dt) {
        $label = $type_labels[$dt] ?? htmlspecialchars($dt);
        echo "<th>$label</th>";
    }
    echo '</tr>';

    foreach ($pivoted as $row) {
        $time = htmlspecialchars($row['observed_at']);
        $sid = $row['source_id'];
        $letter = $source_map[$sid]['letter'] ?? '?';
        echo "<tr><td>$time</td>";
        if ($show_src) echo "<td>$letter</td>";
        foreach ($data_types as $dt) {
            $val = isset($row[$dt]) ? htmlspecialchars(number_format((float)$row[$dt], 2)) : '';
            echo "<td>$val</td>";
        }
        echo "</tr>\n";
    }
    echo '</table>';
    echo '<p style="font-size:.85rem;color:#666">' . count($pivoted) . ' rows</p>';
}

// Source legend
if ($show_src ?? false) {
    echo '<h3 style="margin-top:1rem">Sources</h3>';
    echo '<table class="desc-table">';
    foreach ($source_map as $sid => $info) {
        $esc_name = htmlspecialchars($info['name']);
        $esc_agency = htmlspecialchars($info['agency'] ?? '');
        echo "<tr><td>{$info['letter']}</td><td><a href=\"/source.php?id=$sid\">$esc_name</a></td><td>$esc_agency</td></tr>\n";
    }
    echo '</table>';
}

echo '<p style="margin-top:1rem">';
echo '<a href="/description.php?id=' . $id . '">Description</a>';
echo ' | <a href="/reach.php?id=' . $id . '">Reach details</a>';
echo ' | <a href="/index.html">Back to main page</a></p>';

include_footer();
