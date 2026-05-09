<?php
declare(strict_types=1);
/**
 * Custom gauges page — renders a gauges table for arbitrary gauge IDs.
 *
 * URL format: /custom_gauges.php?ids=1,2,3  (bookmarkable, shareable)
 *
 * Mirrors public_html/gauges.html (built by `levels build`) but limited to
 * the URL-supplied gauge ids and rendered in URL order. Sparkline cells
 * reuse the data-gid placeholder pattern that levels.js lazy-loads from
 * /static/sparklines.json.
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

$STATE_ABBREVS = [
    'AZ' => 'Arizona',  'CA' => 'California', 'CO' => 'Colorado',
    'ID' => 'Idaho',    'KS' => 'Kansas',     'MT' => 'Montana',
    'NV' => 'Nevada',   'NM' => 'New Mexico', 'OR' => 'Oregon',
    'UT' => 'Utah',     'WA' => 'Washington', 'WY' => 'Wyoming',
];

$raw = filter_input(INPUT_GET, 'ids', FILTER_DEFAULT) ?? '';
$ids = array_values(array_unique(array_filter(
    array_map('intval', explode(',', $raw)),
    fn($v) => $v > 0
)));

if (!$ids) {
    header('Location: /gauge_picker.php');
    exit;
}

// Cap at 200 (matches custom.php — keeps memory + AJAX response size sane).
$ids = array_slice($ids, 0, 200);

$db = get_db();
$placeholders = implode(',', array_fill(0, count($ids), '?'));

// --- Gauge metadata + latest observations in one query --------------------
$sql = <<<SQL
SELECT g.id,
       COALESCE(g.river,    g.name)              AS river,
       COALESCE(g.location, '')                  AS location,
       g.state                                   AS state_abbrev,
       g.huc                                     AS huc,
       lo_flow.value                             AS flow,
       lo_flow.observed_at                       AS flow_time,
       lo_inflow.value                           AS inflow,
       lo_inflow.observed_at                     AS inflow_time,
       lo_gage.value                             AS gage,
       lo_gage.observed_at                       AS gage_time,
       lo_temp.value                             AS temperature,
       lo_temp.observed_at                       AS temp_time
FROM gauge g
LEFT JOIN latest_gauge_observation lo_flow
       ON g.id = lo_flow.gauge_id   AND lo_flow.data_type   = 'flow'
LEFT JOIN latest_gauge_observation lo_inflow
       ON g.id = lo_inflow.gauge_id AND lo_inflow.data_type = 'inflow'
LEFT JOIN latest_gauge_observation lo_gage
       ON g.id = lo_gage.gauge_id   AND lo_gage.data_type   = 'gauge'
LEFT JOIN latest_gauge_observation lo_temp
       ON g.id = lo_temp.gauge_id   AND lo_temp.data_type   = 'temperature'
WHERE g.id IN ($placeholders)
SQL;

$stmt = $db->prepare($sql);
$stmt->execute($ids);
$gauges_by_id = [];
foreach ($stmt->fetchAll() as $row) {
    $gauges_by_id[(int)$row['id']] = $row;
}

// --- Status rollup (matches gauges.html: count low/okay/high across the
// gauge's reaches and pick a representative label) ------------------------
$status_sql = <<<SQL
SELECT r.gauge_id,
       SUM(CASE WHEN s = 'low'  THEN 1 ELSE 0 END) AS n_low,
       SUM(CASE WHEN s = 'okay' THEN 1 ELSE 0 END) AS n_okay,
       SUM(CASE WHEN s = 'high' THEN 1 ELSE 0 END) AS n_high
FROM (
  SELECT r.gauge_id,
         CASE
           WHEN rc.low IS NULL OR lo.value IS NULL THEN NULL
           WHEN lo.value < rc.low  THEN 'low'
           WHEN lo.value > rc.high THEN 'high'
           ELSE 'okay'
         END AS s
  FROM reach r
  LEFT JOIN latest_gauge_observation lo
         ON r.gauge_id = lo.gauge_id AND lo.data_type = 'flow'
  LEFT JOIN (
    SELECT reach_id, MIN(low) AS low, MAX(high) AS high
    FROM reach_class
    WHERE low_data_type = 'flow' AND low IS NOT NULL AND high IS NOT NULL
    GROUP BY reach_id
  ) rc ON rc.reach_id = r.id
  WHERE r.gauge_id IN ($placeholders)
) r
GROUP BY r.gauge_id
SQL;
$status_stmt = $db->prepare($status_sql);
$status_stmt->execute($ids);
$status_by_gauge = [];
foreach ($status_stmt->fetchAll() as $r) {
    $gid = (int)$r['gauge_id'];
    $n_low  = (int)$r['n_low'];
    $n_okay = (int)$r['n_okay'];
    $n_high = (int)$r['n_high'];
    if ($n_okay > 0) {
        $label = 'okay';
    } elseif ($n_low === 0 && $n_high === 0) {
        $label = null;
    } else {
        $label = $n_low >= $n_high ? 'low' : 'high';
    }
    $status_by_gauge[$gid] = [
        'label'  => $label,
        'counts' => array_filter(['low' => $n_low, 'okay' => $n_okay, 'high' => $n_high]),
    ];
}

// --- Build rows in URL order ---------------------------------------------
$rows = [];
foreach ($ids as $id) {
    if (isset($gauges_by_id[$id])) {
        $rows[] = $gauges_by_id[$id];
    }
}

// Collect filter-pill values that actually appear on the page.
$states_present = [];
$huc8_present   = [];
$has_no_huc     = false;
foreach ($rows as $r) {
    $abbrev = (string)($r['state_abbrev'] ?? '');
    if ($abbrev !== '' && isset($STATE_ABBREVS[$abbrev])) {
        $states_present[$STATE_ABBREVS[$abbrev]] = true;
    }
    $huc = (string)($r['huc'] ?? '');
    if (strlen($huc) >= 8) {
        $huc8_present[substr($huc, 0, 8)] = true;
    } else {
        $has_no_huc = true;
    }
}
ksort($states_present);

// HUC8 / HUC6 names. PHP coerces all-digit array keys to int, so cast back
// to string at every read site (htmlspecialchars/substr require string).
$huc8_names = [];
$huc6_names = [];
if ($huc8_present) {
    $huc8_codes = array_map('strval', array_keys($huc8_present));
    $hp = implode(',', array_fill(0, count($huc8_codes), '?'));
    $hn8 = $db->prepare("SELECT code, name FROM huc_name WHERE level = 8 AND code IN ($hp)");
    $hn8->execute($huc8_codes);
    foreach ($hn8->fetchAll() as $r) $huc8_names[(string)$r['code']] = $r['name'];

    $huc6_codes = array_values(array_unique(array_map(fn($c) => substr($c, 0, 6), $huc8_codes)));
    $hp6 = implode(',', array_fill(0, count($huc6_codes), '?'));
    $hn6 = $db->prepare("SELECT code, name FROM huc_name WHERE level = 6 AND code IN ($hp6)");
    $hn6->execute($huc6_codes);
    foreach ($hn6->fetchAll() as $r) $huc6_names[(string)$r['code']] = $r['name'];
}

// Group HUC8s under HUC6 parents (sorted by parent name)
$huc6_groups = [];
foreach ($huc8_codes ?? [] as $h8) {
    $h6 = substr($h8, 0, 6);
    $huc6_groups[$h6][] = [$h8, $huc8_names[$h8] ?? $h8];
}
uksort($huc6_groups, fn($a, $b) => strcmp(
    $huc6_names[(string)$a] ?? (string)$a,
    $huc6_names[(string)$b] ?? (string)$b
));
foreach ($huc6_groups as &$arr) {
    sort($arr);  // by huc8 code
}
unset($arr);

header('Cache-Control: max-age=60');
include_header('Custom Gauges Page', '', '', '', ['picker_kind' => 'gauge']);
$id_param = htmlspecialchars($raw);
?>
<h2>Custom Gauges Page</h2>
<p style="margin:.3rem 0 .5rem;font-size:.85rem">
  <a href="/gauge_picker.php?ids=<?= $id_param ?>">Edit selection</a> |
  <a href="/gauges.html">All gauges</a> |
  <a href="/index.html">Home</a>
  | <?= count($rows) ?> gauge<?= count($rows) !== 1 ? 's' : '' ?>
</p>

<?php
$fg_toggle = '<span class="fg-toggle">'
           . '<button type="button" data-all>All</button>'
           . '<button type="button" data-none>None</button>'
           . '</span>';
?>
<div class="filter-bar" id="filter-bar" hidden>
<?php if (count($states_present) > 1): ?>
  <details class="filter-group">
    <summary>State <span class="fg-count"><?= count($states_present) ?></span></summary>
    <div class="filter-pills" data-group="state">
      <?= $fg_toggle ?>
<?php foreach (array_keys($states_present) as $st): ?>
      <label><input type="checkbox" value="<?= htmlspecialchars($st) ?>" checked><?= htmlspecialchars($st) ?></label>
<?php endforeach; ?>
    </div>
  </details>
<?php endif; ?>
<?php if ($huc6_groups || $has_no_huc):
    $total = array_sum(array_map('count', $huc6_groups)) + ($has_no_huc ? 1 : 0); ?>
  <details class="filter-group" open>
    <summary>Watershed <span class="fg-count"><?= $total ?></span></summary>
    <div class="filter-pills" data-group="huc8">
      <?= $fg_toggle ?>
<?php foreach ($huc6_groups as $h6 => $children):
        $h6 = (string)$h6;
        $h6_name = $huc6_names[$h6] ?? $h6; ?>
      <details class="filter-subgroup">
        <summary><label class="huc6-parent"><input type="checkbox" data-huc6="<?= htmlspecialchars($h6) ?>" checked><?= htmlspecialchars($h6_name) ?></label> <span class="fg-count"><?= count($children) ?></span></summary>
        <div class="filter-pills-sub">
<?php foreach ($children as [$h8, $h8_name]): ?>
          <label><input type="checkbox" value="<?= htmlspecialchars($h8) ?>" checked><?= htmlspecialchars($h8_name) ?></label>
<?php endforeach; ?>
        </div>
      </details>
<?php endforeach; ?>
<?php if ($has_no_huc): ?>
      <div class="filter-pills-sub no-huc-row">
        <label><input type="checkbox" value="" checked>(no HUC)</label>
      </div>
<?php endif; ?>
    </div>
  </details>
<?php endif; ?>
  <div class="filter-meta" aria-live="polite">
    <span class="fb-count"></span>
    <button type="button" class="fb-reset">Reset</button>
  </div>
</div>

<table class="levels">
<thead><tr>
  <th scope="col">Status</th>
  <th scope="col">River</th>
  <th scope="col">Location</th>
  <th scope="col">Date</th>
  <th scope="col">Flow<br>cfs</th>
  <th scope="col" class="secondary">2-day Trend</th>
  <th scope="col">Gauge<br>ft</th>
  <th scope="col">Temp<br>&deg;F</th>
</tr></thead>
<tbody>
<?php foreach ($rows as $r):
    $gid = (int)$r['id'];
    $abbrev = (string)($r['state_abbrev'] ?? '');
    $state = $STATE_ABBREVS[$abbrev] ?? '';
    $huc8 = strlen((string)$r['huc']) >= 8 ? substr($r['huc'], 0, 8) : '';

    // Status cell
    $status_info = $status_by_gauge[$gid] ?? null;
    $status_word = $status_info['label'] ?? null;
    if ($status_word) {
        $counts = $status_info['counts'] ?? [];
        $count_summary = implode(', ', array_map(fn($k, $v) => "$v $k", array_keys($counts), array_values($counts)));
        $title = $count_summary ? ' title="' . htmlspecialchars($count_summary) . '"' : '';
        $status_cell = '<span class="level-' . htmlspecialchars($status_word) . '"' . $title . '>' . htmlspecialchars($status_word) . '</span>';
    } else {
        $status_cell = '';
    }

    // Best-available time (matches gauges.html: latest of the four obs)
    $times = array_filter([$r['flow_time'], $r['inflow_time'], $r['gage_time'], $r['temp_time']]);
    $time_html = '';
    if ($times) {
        $latest = max(array_map('strtotime', $times));
        $iso = gmdate('Y-m-d\TH:i:s\Z', $latest);
        $disp = gmdate('m/d H:i', $latest);
        $time_html = "<time datetime=\"$iso\">$disp</time>";
    }

    // Flow cell — prefer flow, fall back to inflow, then gage (as feet) like
    // gauges.html does in _build_gauges_table.
    $flow_val = $r['flow'] ?? $r['inflow'];
    $gage_val = $r['gage'];
    if ($flow_val !== null) {
        $flow_cell = number_format((float)$flow_val, 0);
    } elseif ($gage_val !== null) {
        $flow_cell = number_format((float)$gage_val, 1) . '&prime;';
    } else {
        $flow_cell = '';
    }

    $gage_cell = $gage_val !== null ? number_format((float)$gage_val, 1) : '';
    $temp_cell = $r['temperature'] !== null ? number_format((float)$r['temperature'], 1) : '';

    $attrs = '';
    if ($state !== '' && $huc8 !== '') {
        $attrs = ' data-state="' . htmlspecialchars($state) . '" data-huc8="' . htmlspecialchars($huc8) . '"';
    }
    $status_attr = $status_word ? ' data-status="' . htmlspecialchars($status_word) . '"' : '';
?>
<tr class="clickable-row" data-href="/gauge.php?id=<?= $gid ?>"<?= $attrs ?><?= $status_attr ?>>
  <td class="td-status" data-label="Status"><?= $status_cell ?></td>
  <td class="td-name" data-label="River"><a href="/gauge.php?id=<?= $gid ?>"><?= htmlspecialchars($r['river']) ?></a></td>
  <td data-label="Location"><?= htmlspecialchars($r['location']) ?></td>
  <td class="td-date" data-label="Date"><?= $time_html ?></td>
  <td class="td-flow" data-label="Flow"><?= $flow_cell ?></td>
  <td class="td-spark secondary" data-label="2-day Trend"><span class="spark" data-gid="<?= $gid ?>"></span></td>
  <td class="td-gage" data-label="Gauge"><?= $gage_cell ?></td>
  <td class="td-temp" data-label="Temp"><?= $temp_cell ?></td>
</tr>
<?php endforeach; ?>
</tbody>
</table>

<?php
$filters_mtime = @filemtime($_SERVER['DOCUMENT_ROOT'] . '/static/filters.js') ?: 1;
?>
<script src="/static/filters.js?v=<?= $filters_mtime ?>" defer></script>
<?php
include_footer();
