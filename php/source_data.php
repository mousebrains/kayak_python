<?php
declare(strict_types=1);
/**
 * Per-source data inspector — raw observations for a single source.
 *
 * Usage: /source_data.php?id=<source_id>[&start=YYYY-MM-DD&end=YYYY-MM-DD][&sort=asc|desc]
 *
 * Mirrors data.php's shape but scopes to one source instead of joining
 * through reach → gauge → all linked sources. Data types pivot into
 * columns (flow / gauge / temperature / …) the same way data.php does.
 * Linked from source.php's Observations table.
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';
require_once __DIR__ . '/includes/validate.php';

$id         = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT);
$start_raw  = filter_input(INPUT_GET, 'start', FILTER_SANITIZE_SPECIAL_CHARS);
$end_raw    = filter_input(INPUT_GET, 'end', FILTER_SANITIZE_SPECIAL_CHARS);
$start_date = validate_date(is_string($start_raw) ? $start_raw : null);
$end_date   = validate_date(is_string($end_raw)   ? $end_raw   : null);
$sort       = filter_input(INPUT_GET, 'sort', FILTER_SANITIZE_SPECIAL_CHARS) === 'asc' ? 'asc' : 'desc';

if (!$id) { http_response_code(400); exit('Missing id parameter'); }

$db = get_db();
$stmt = $db->prepare('SELECT id, name, agency FROM source WHERE id = ?');
$stmt->execute([$id]);
$source = $stmt->fetch();
if (!$source) { http_response_code(404); exit('Source not found'); }

$name = (string)$source['name'];

// Default date range: last 2 days (matches data.php).
$default_end   = date('Y-m-d');
$default_start = date('Y-m-d', time() - 2 * 86400);
$form_start    = $start_date ?: $default_start;
$form_end      = $end_date   ?: $default_end;
// validate_date / the date() default guarantee parseable Y-m-d strings;
// the cast is to satisfy PHPStan (strtotime is int|false-typed).
$since         = date('Y-m-d 00:00:00', (int)strtotime($form_start));
$until         = date('Y-m-d 23:59:59', (int)strtotime($form_end));

$stmt = $db->prepare(
    'SELECT data_type, value, observed_at FROM observation
     WHERE source_id = ? AND observed_at >= ? AND observed_at <= ?
     ORDER BY observed_at ' . ($sort === 'asc' ? 'ASC' : 'DESC') . '
     LIMIT 10000'
);
$stmt->execute([$id, $since, $until]);
$rows = $stmt->fetchAll();

// Pivot data_types into columns, keyed by observed_at (single source so the
// data.php (observed_at, source_id) compound key collapses to just observed_at).
$data_types = [];
$pivoted    = [];
foreach ($rows as $r) {
    $key = $r['observed_at'];
    $data_types[$r['data_type']] = true;
    if (!isset($pivoted[$key])) {
        $pivoted[$key] = ['observed_at' => $r['observed_at']];
    }
    $pivoted[$key][$r['data_type']] = $r['value'];
}
$data_types = array_keys($data_types);
sort($data_types);

$type_labels = [
    'flow'        => 'Flow',
    'gauge'       => 'Gage Ht',
    'temperature' => 'Temp',
    'inflow'      => 'Inflow',
    'outflow'     => 'Outflow',
];

header('Cache-Control: no-cache');
include_header("$name - Data", '', '', '', ['picker_kind' => 'gauge']);

echo '<h2>' . htmlspecialchars($name) . ' — Data Inspector</h2>';
echo '<p style="font-size:.85rem;color:var(--c-text-muted)">'
    . htmlspecialchars((string)($source['agency'] ?? ''))
    . ' source #' . $id
    . ' · <a href="/source.php?id=' . $id . '">source details</a></p>';

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
    $sort_arrow  = $sort === 'desc' ? ' ▼' : ' ▲';
    $sort_url    = '?id=' . $id
                 . '&start=' . urlencode($form_start)
                 . '&end='   . urlencode($form_end)
                 . '&sort='  . $toggle_sort;
    echo '<table class="readings-table">';
    echo '<tr><th><a href="' . htmlspecialchars($sort_url) . '" style="color:inherit;text-decoration:none">Time' . $sort_arrow . '</a></th>';
    foreach ($data_types as $dt) {
        $label = $type_labels[$dt] ?? htmlspecialchars($dt);
        echo "<th>$label</th>";
    }
    echo '</tr>';

    foreach ($pivoted as $row) {
        $ts   = strtotime($row['observed_at']);
        $time = $ts ? date('Y-m-d H:i:s', $ts) : htmlspecialchars($row['observed_at']);
        echo "<tr><td>$time</td>";
        foreach ($data_types as $dt) {
            if (!isset($row[$dt])) {
                $val = '';
            } elseif (in_array($dt, ['flow', 'inflow', 'outflow'], true)) {
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

echo '<p style="margin-top:1rem">';
echo '<a href="/source.php?id=' . $id . '">Back to source</a>';
echo ' | <a href="/index.html">Back to main page</a></p>';

include_footer();
