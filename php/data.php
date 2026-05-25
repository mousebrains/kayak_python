<?php
declare(strict_types=1);
/**
 * Data inspector — raw observation data for a reach's gauge sources.
 *
 * Usage: /data.php?id=<reach_id>[&start=YYYY-MM-DD&end=YYYY-MM-DD]
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';
require_once __DIR__ . '/includes/validate.php';

$id = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT);
$start_date = validate_date(filter_input(INPUT_GET, 'start', FILTER_SANITIZE_SPECIAL_CHARS));
$end_date = validate_date(filter_input(INPUT_GET, 'end', FILTER_SANITIZE_SPECIAL_CHARS));
$sort = filter_input(INPUT_GET, 'sort', FILTER_SANITIZE_SPECIAL_CHARS) === 'asc' ? 'asc' : 'desc';

if (!$id) { http_response_code(400); exit('Missing id parameter'); }

$db = get_db();
$reach = get_reach_or_404($id);

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
    $letter_idx = 0;
    foreach ($sources as $s) {
        $source_ids[] = $s['id'];
        $source_map[$s['id']] = [
            'name' => $s['name'],
            'agency' => $s['agency'],
            'letter' => chr(ord('A') + $letter_idx++),
        ];
    }
}

if (!$source_ids) {
    header('Cache-Control: no-cache');
    include_header("$name - Data", '', '', '', ['picker_kind' => 'gauge']);
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
$since = date('Y-m-d 00:00:00', date_ts($form_start));
$until = date('Y-m-d 23:59:59', date_ts($form_end));

// Query observations for all sources in range
$placeholders = implode(',', array_fill(0, count($source_ids), '?'));
$params = array_merge($source_ids, [$since, $until]);
$stmt = $db->prepare(
    "SELECT source_id, data_type, value, observed_at FROM observation
     WHERE source_id IN ($placeholders) AND observed_at >= ? AND observed_at <= ?
     ORDER BY observed_at " . ($sort === 'asc' ? 'ASC' : 'DESC') . "
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
$show_src = count($source_map) > 1;

$type_labels = [
    'flow' => 'Flow',
    'gauge' => 'Gage Ht',
    'temperature' => 'Temp',
    'inflow' => 'Inflow',
    'outflow' => 'Outflow',
];

header('Cache-Control: no-cache');
include_header($name . ' - Data', '', '', '', ['picker_kind' => 'gauge']);

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
    $toggle_sort = $sort === 'desc' ? 'asc' : 'desc';
    $sort_arrow = $sort === 'desc' ? ' ▼' : ' ▲';
    $sort_url = '?id=' . $id . '&start=' . urlencode($form_start) . '&end=' . urlencode($form_end) . '&sort=' . $toggle_sort;
    echo '<table class="readings-table">';
    echo '<tr><th><a href="' . htmlspecialchars($sort_url) . '" style="color:inherit;text-decoration:none">Time' . $sort_arrow . '</a></th>';
    if ($show_src) echo '<th>Src</th>';
    foreach ($data_types as $dt) {
        $label = $type_labels[$dt] ?? htmlspecialchars($dt);
        echo "<th>$label</th>";
    }
    echo '</tr>';

    foreach ($pivoted as $row) {
        $ts = strtotime($row['observed_at']);
        $time = $ts ? date('Y-m-d H:i:s', $ts) : htmlspecialchars($row['observed_at']);
        $sid = $row['source_id'];
        $letter = $source_map[$sid]['letter'] ?? '?';
        echo "<tr><td>$time</td>";
        if ($show_src) echo "<td>$letter</td>";
        foreach ($data_types as $dt) {
            if (!isset($row[$dt])) {
                $val = '';
            } elseif (in_array($dt, ['flow', 'inflow', 'outflow'])) {
                $val = number_format((float)$row[$dt], 0);
            } else {
                $val = number_format((float)$row[$dt], 1);
            }
            echo "<td>$val</td>";
        }
        echo "</tr>\n";
    }
    echo '</table>';
    echo '<p style="font-size:.85rem;color:var(--c-text-muted)">' . count($pivoted) . ' rows</p>';
}

// Source legend
if ($show_src) {
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
