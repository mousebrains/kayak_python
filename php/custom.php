<?php
/**
 * Custom levels page — renders a levels table for arbitrary section IDs.
 *
 * URL format: /custom.php?ids=237,339,340  (bookmarkable, shareable)
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

$raw = filter_input(INPUT_GET, 'ids', FILTER_DEFAULT) ?? '';
$ids = array_values(array_unique(array_filter(
    array_map('intval', explode(',', $raw)),
    fn($v) => $v > 0
)));

if (!$ids) {
    header('Location: /picker.php');
    exit;
}

// Cap at 500 sections
$ids = array_slice($ids, 0, 500);

$db = get_db();
$placeholders = implode(',', array_fill(0, count($ids), '?'));

$sql = <<<SQL
SELECT sec.id,
       COALESCE(sec.display_name, sec.name) AS display_name,
       sec.sort_name,
       sec.basin AS drainage,
       g.location AS gauge_location,
       lo_flow.value          AS flow,
       lo_flow.delta_per_hour AS flow_delta,
       lo_gage.value          AS gage,
       lo_temp.value          AS temperature,
       lo_flow.observed_at    AS flow_time,
       lo_gage.observed_at    AS gage_time,
       lo_temp.observed_at    AS temp_time
FROM section sec
LEFT JOIN gauge g ON sec.gauge_id = g.id
LEFT JOIN (
    SELECT gauge_id, MIN(source_id) AS source_id
    FROM gauge_source GROUP BY gauge_id
) gs ON g.id = gs.gauge_id
LEFT JOIN latest_observation lo_flow
       ON gs.source_id = lo_flow.source_id AND lo_flow.data_type = 'flow'
LEFT JOIN latest_observation lo_gage
       ON gs.source_id = lo_gage.source_id AND lo_gage.data_type = 'gauge'
LEFT JOIN latest_observation lo_temp
       ON gs.source_id = lo_temp.source_id AND lo_temp.data_type = 'temperature'
WHERE sec.id IN ($placeholders)
ORDER BY sec.sort_name
SQL;

$stmt = $db->prepare($sql);
$stmt->execute($ids);
$sections = $stmt->fetchAll();

// Load classes for all sections in one query
$class_sql = "SELECT section_id, GROUP_CONCAT(name, ', ') AS class
              FROM section_class WHERE section_id IN ($placeholders)
              GROUP BY section_id";
$cls_stmt = $db->prepare($class_sql);
$cls_stmt->execute($ids);
$classes = [];
foreach ($cls_stmt->fetchAll() as $row) {
    $classes[$row['section_id']] = $row['class'];
}

header('Cache-Control: max-age=60');
include_header('Custom Levels Page');

$id_param = htmlspecialchars($raw);
?>
<h2>Custom Levels Page</h2>
<p style="margin:.3rem 0 .5rem;font-size:.85rem">
  <a href="/picker.php">Edit selection</a> | <a href="/index.html">Home</a>
  | <?= count($sections) ?> section<?= count($sections) !== 1 ? 's' : '' ?>
</p>

<table class="levels">
<thead><tr>
  <th>Status</th>
  <th>Name</th>
  <th>Location</th>
  <th>Date</th>
  <th><a href="#Units">Flow<br>CFS</a></th>
  <th><a href="#Units">Height<br>Feet</a></th>
  <th><a href="#Units">Temp<br>F</a></th>
  <th class="secondary">Drainage</th>
  <th class="secondary">Class</th>
</tr></thead>
<tbody>
<?php foreach ($sections as $s):
    $id = (int)$s['id'];

    // Status from flow delta
    $status = '';
    if ($s['flow_delta'] !== null) {
        $dph = (float)$s['flow_delta'];
        if (abs($dph) < 0.5) {
            $status = '<span class="stable">stable</span>';
        } elseif ($dph > 0) {
            $status = '<span class="rising">rising</span>';
        } else {
            $status = '<span class="falling">falling</span>';
        }
    }

    // Best available timestamp
    $time_str = '';
    $ts = $s['flow_time'] ?? $s['gage_time'] ?? $s['temp_time'] ?? null;
    if ($ts) {
        $time_str = date('m/d H:i', strtotime($ts));
    }

    // Values
    $name = htmlspecialchars($s['display_name'] ?? '');
    $loc  = htmlspecialchars($s['gauge_location'] ?? '');
    $flow = $s['flow'] !== null ? '<a href="/plot.php?type=flow&id=' . $id . '">' . number_format((float)$s['flow'], 0) . '</a>' : '';
    $gage = $s['gage'] !== null ? '<a href="/plot.php?type=gage&id=' . $id . '">' . number_format((float)$s['gage'], 2) . '</a>' : '';
    $temp = $s['temperature'] !== null ? '<a href="/plot.php?type=temp&id=' . $id . '">' . number_format((float)$s['temperature'], 0) . '</a>' : '';
    $drain = htmlspecialchars($s['drainage'] ?? '');
    $class = htmlspecialchars($classes[$id] ?? '');
?>
<tr>
  <td class="td-status" data-label="Status"><?= $status ?></td>
  <td class="td-name" data-label="Name"><a href="/description.php?id=<?= $id ?>"><?= $name ?></a></td>
  <td data-label="Location"><?= $loc ?></td>
  <td class="td-date" data-label="Date"><?= $time_str ?></td>
  <td class="td-flow" data-label="Flow"><?= $flow ?></td>
  <td class="td-gage" data-label="Height"><?= $gage ?></td>
  <td class="td-temp" data-label="Temp"><?= $temp ?></td>
  <td class="secondary" data-label="Drainage"><?= $drain ?></td>
  <td class="secondary" data-label="Class"><?= $class ?></td>
</tr>
<?php endforeach; ?>
</tbody>
</table>

<p id="Units" style="margin-top:.5rem;font-size:.75rem;color:#888">
  CFS = cubic feet per second. Feet = gage height in feet. F = Fahrenheit.
</p>
<?php
include_footer();
