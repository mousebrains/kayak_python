<?php
/**
 * Custom levels page — renders a levels table for arbitrary reach IDs.
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

// Cap at 200 reaches (500 caused OOM with 128MB limit due to sparkline queries)
$ids = array_slice($ids, 0, 200);

$db = get_db();
$placeholders = implode(',', array_fill(0, count($ids), '?'));

$sql = <<<SQL
SELECT r.id,
       COALESCE(r.display_name, r.name) AS display_name,
       r.sort_name,
       r.basin AS drainage,
       g.location AS gauge_location,
       lo_flow.value          AS flow,
       lo_flow.delta_per_hour AS flow_delta,
       lo_gage.value          AS gage,
       lo_temp.value          AS temperature,
       lo_flow.observed_at    AS flow_time,
       lo_gage.observed_at    AS gage_time,
       lo_temp.observed_at    AS temp_time
FROM reach r
LEFT JOIN gauge g ON r.gauge_id = g.id
LEFT JOIN latest_gauge_observation lo_flow
       ON g.id = lo_flow.gauge_id AND lo_flow.data_type = 'flow'
LEFT JOIN latest_gauge_observation lo_gage
       ON g.id = lo_gage.gauge_id AND lo_gage.data_type = 'gauge'
LEFT JOIN latest_gauge_observation lo_temp
       ON g.id = lo_temp.gauge_id AND lo_temp.data_type = 'temperature'
WHERE r.id IN ($placeholders)
ORDER BY r.sort_name
SQL;

$stmt = $db->prepare($sql);
$stmt->execute($ids);
$reaches = $stmt->fetchAll();

// Load classes for all reaches in one query
$group_expr = "GROUP_CONCAT(name, ', ')";
$class_sql = "SELECT reach_id, $group_expr AS class
              FROM reach_class WHERE reach_id IN ($placeholders)
              GROUP BY reach_id";
$cls_stmt = $db->prepare($class_sql);
$cls_stmt->execute($ids);
$classes = [];
foreach ($cls_stmt->fetchAll() as $row) {
    $classes[$row['reach_id']] = $row['class'];
}

// Map reach_id -> gauge_id and collect distinct gauge source_ids for sparklines
$gauge_map = [];
$gid_stmt = $db->prepare("SELECT id, gauge_id FROM reach WHERE id IN ($placeholders)");
$gid_stmt->execute($ids);
foreach ($gid_stmt->fetchAll() as $row) {
    if ($row['gauge_id']) $gauge_map[(int)$row['id']] = (int)$row['gauge_id'];
}

// Get primary source_id for each gauge (same logic as main query)
$gauge_ids = array_values(array_unique(array_filter(array_values($gauge_map))));
$sparklines = [];
if ($gauge_ids) {
    $gph = implode(',', array_fill(0, count($gauge_ids), '?'));
    $src_stmt = $db->prepare("SELECT gauge_id, MIN(source_id) AS source_id FROM gauge_source WHERE gauge_id IN ($gph) GROUP BY gauge_id");
    $src_stmt->execute($gauge_ids);
    $gauge_sources = [];
    foreach ($src_stmt->fetchAll() as $row) {
        $gauge_sources[(int)$row['gauge_id']] = (int)$row['source_id'];
    }

    // Fetch sparkline data per source, sampled to ~60 points (every 48min over 48h)
    $spark_stmt = $db->prepare(
        "SELECT value, observed_at FROM observation
         WHERE source_id = ? AND data_type = 'flow'
           AND observed_at >= datetime('now', '-48 hours')
         ORDER BY observed_at"
    );
    foreach ($gauge_sources as $gid => $sid) {
        $spark_stmt->execute([$sid]);
        $all = [];
        while ($row = $spark_stmt->fetch()) {
            $all[] = ['ts' => strtotime($row['observed_at']), 'v' => (float)$row['value']];
        }
        // Downsample to ~60 points
        $n = count($all);
        if ($n > 60) {
            $step = $n / 60;
            $sampled = [];
            for ($i = 0; $i < 60; $i++) {
                $sampled[] = $all[(int)($i * $step)];
            }
            $sampled[] = $all[$n - 1]; // always include last point
            $all = $sampled;
        }
        if (count($all) >= 3) {
            $sparklines[$gid] = $all;
        }
    }
}

// Build SVG sparkline for a gauge
function build_sparkline(array $data, int $w = 80, int $h = 20): string {
    if (count($data) < 3) return '';
    $xs = array_column($data, 'ts');
    $ys = array_column($data, 'v');
    $x_min = min($xs); $x_max = max($xs);
    $y_min = min($ys); $y_max = max($ys);
    $x_range = $x_max - $x_min ?: 1;
    $y_range = $y_max - $y_min ?: 1;
    $pts = [];
    foreach ($data as $d) {
        $px = (int)(($d['ts'] - $x_min) / $x_range * $w);
        $py = (int)($h - ($d['v'] - $y_min) / $y_range * $h);
        $pts[] = "$px,$py";
    }
    $points = implode(' ', $pts);
    return '<svg class="spark" width="' . $w . '" height="' . $h
         . '" viewBox="0 0 ' . $w . ' ' . $h . '">'
         . '<polyline fill="none" stroke="#2060A0" stroke-width="1.5" points="' . $points . '"/>'
         . '</svg>';
}

header('Cache-Control: max-age=60');
include_header('Custom Levels Page');

$id_param = htmlspecialchars($raw);
?>
<h2>Custom Levels Page</h2>
<p style="margin:.3rem 0 .5rem;font-size:.85rem">
  <a href="/picker.php">Edit selection</a> | <a href="/index.html">Home</a>
  | <?= count($reaches) ?> reach<?= count($reaches) !== 1 ? 'es' : '' ?>
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
<?php foreach ($reaches as $s):
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

    // Best available timestamp — render as <time> for client-side local conversion
    $time_html = '';
    $ts = $s['flow_time'] ?? $s['gage_time'] ?? $s['temp_time'] ?? null;
    if ($ts) {
        $iso = date('Y-m-d\TH:i:s\Z', strtotime($ts));
        $display = date('m/d H:i', strtotime($ts));
        $time_html = "<time datetime=\"$iso\">$display</time>";
    }

    // Values
    $name = htmlspecialchars($s['display_name'] ?? '');
    $loc  = htmlspecialchars($s['gauge_location'] ?? '');
    // Sparkline
    $spark = '';
    $gid = $gauge_map[$id] ?? null;
    if ($gid && isset($sparklines[$gid])) {
        $spark = build_sparkline($sparklines[$gid]);
    }

    $flow_val = $s['flow'] !== null ? number_format((float)$s['flow'], 0) : '';
    $flow = $flow_val !== '' ? '<a href="/plot.php?type=flow&id=' . $id . '">' . $flow_val . '</a>' . $spark : '';
    $gage = $s['gage'] !== null ? '<a href="/plot.php?type=gage&id=' . $id . '">' . number_format((float)$s['gage'], 2) . '</a>' : '';
    $temp = $s['temperature'] !== null ? '<a href="/plot.php?type=temp&id=' . $id . '">' . number_format((float)$s['temperature'], 0) . '</a>' : '';
    $drain = htmlspecialchars($s['drainage'] ?? '');
    $class = htmlspecialchars($classes[$id] ?? '');
?>
<tr class="clickable-row" data-href="/description.php?id=<?= $id ?>">
  <td class="td-status" data-label="Status"><?= $status ?></td>
  <td class="td-name" data-label="Name"><a href="/description.php?id=<?= $id ?>"><?= $name ?></a></td>
  <td data-label="Location"><?= $loc ?></td>
  <td class="td-date" data-label="Date"><?= $time_html ?></td>
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
