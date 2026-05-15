<?php

declare(strict_types=1);

/**
 * Handler for /custom.php — renders a levels table for arbitrary
 * reach IDs supplied via `?ids=CSV`.
 *
 * Called from custom.php after arg-parse + empty-redirect. Loads
 * reach summary rows + classes + sparkline data, reorders by URL
 * position (drag-reorder contract from picker.php), and renders the
 * filterable levels table.
 *
 * Convention matches the other helpers in this directory: function-only
 * (no top-level side effects beyond require_once), snake_case names,
 * strict types, helpers prefixed with `_` are file-private and carry
 * the file's name as part of the prefix (see Tier 5 CI-lesson note in
 * docs/done/PLAN_php_layer_split.md).
 */

require_once __DIR__ . '/db.php';
require_once __DIR__ . '/header.php';
require_once __DIR__ . '/footer.php';
require_once __DIR__ . '/class_tiers.php';

/** Status-pill metadata — label + swatch hex. Mirrors the inline arrays in the pre-extract file. */
const CUSTOM_LEVELS_STATUS_META = [
    'low'     => ['label' => 'Low',     'swatch' => '#e8a735'],
    'okay'    => ['label' => 'Okay',    'swatch' => '#4caf50'],
    'high'    => ['label' => 'High',    'swatch' => '#e53935'],
    'unknown' => ['label' => 'Unknown', 'swatch' => '#2196F3'],
];

/**
 * Dispatch and write the full HTTP response.
 *
 * @param list<int> $ids  Already-validated, > 0, capped at 200.
 */
function handle_custom_levels(PDO $db, array $ids): void
{
    $reaches = _load_custom_reach_rows($db, $ids);
    $reaches = _custom_reorder_by_position($reaches, $ids);
    [$classes, $tiers_by_reach] = _load_custom_classes($db, $ids);
    [$gauge_map, $sparklines] = _load_custom_sparklines($db, $ids);

    header('Cache-Control: max-age=60');
    include_header('Custom Levels Page');

    _render_custom_header($reaches, $tiers_by_reach);
    _render_custom_table($reaches, $tiers_by_reach, $classes, $sparklines, $gauge_map);
    _render_custom_footer();

    include_footer();
}

/**
 * The big LEFT JOIN — reach + gauge + flow/gage/temp latest obs +
 * status (computed from class_range vs current flow) + first state.
 *
 * @param list<int> $ids
 * @return list<array<string, mixed>>
 */
function _load_custom_reach_rows(PDO $db, array $ids): array
{
    $placeholders = implode(',', array_fill(0, count($ids), '?'));
    $sql = <<<SQL
SELECT r.id,
       COALESCE(r.display_name, r.name) AS display_name,
       r.sort_name,
       r.basin                          AS drainage,
       COALESCE(NULLIF(r.description, ''), g.location) AS gauge_location,
       lo_flow.value                    AS flow,
       lo_flow.delta_per_hour           AS flow_delta,
       lo_gage.value                    AS gage,
       lo_temp.value                    AS temperature,
       lo_flow.observed_at              AS flow_time,
       lo_gage.observed_at              AS gage_time,
       lo_temp.observed_at              AS temp_time,
       CASE
           WHEN rc_range.low IS NULL OR lo_flow.value IS NULL THEN NULL
           WHEN lo_flow.value <  rc_range.low  THEN 'low'
           WHEN lo_flow.value >  rc_range.high THEN 'high'
           ELSE 'okay'
       END                              AS status,
       (SELECT st.name FROM state st
          JOIN reach_state rs ON st.id = rs.state_id
          WHERE rs.reach_id = r.id
          ORDER BY st.name LIMIT 1)     AS state
FROM reach r
LEFT JOIN gauge g ON r.gauge_id = g.id
LEFT JOIN latest_gauge_observation lo_flow
       ON g.id = lo_flow.gauge_id AND lo_flow.data_type = 'flow'
LEFT JOIN latest_gauge_observation lo_gage
       ON g.id = lo_gage.gauge_id AND lo_gage.data_type = 'gauge'
LEFT JOIN latest_gauge_observation lo_temp
       ON g.id = lo_temp.gauge_id AND lo_temp.data_type = 'temperature'
LEFT JOIN (
    SELECT reach_id, MIN(low) AS low, MAX(high) AS high
    FROM reach_class
    WHERE low_data_type = 'flow' AND low IS NOT NULL AND high IS NOT NULL
    GROUP BY reach_id
) rc_range ON rc_range.reach_id = r.id
WHERE r.id IN ($placeholders)
SQL;
    $stmt = $db->prepare($sql);
    $stmt->execute($ids);
    return array_values($stmt->fetchAll());
}

/**
 * Sort rows in URL-supplied position order — picker.php's drag-
 * reorder relies on this. Rows whose IDs are absent from $ids fall
 * to the end (defensive — the WHERE r.id IN (…) clause should make
 * that impossible).
 *
 * @param  list<array<string, mixed>> $reaches
 * @param  list<int>                  $ids
 * @return list<array<string, mixed>>
 */
function _custom_reorder_by_position(array $reaches, array $ids): array
{
    $pos = array_flip($ids);
    usort($reaches, fn($a, $b) =>
        ($pos[$a['id']] ?? PHP_INT_MAX) <=> ($pos[$b['id']] ?? PHP_INT_MAX)
    );
    return $reaches;
}

/**
 * Per-reach class list — both the raw comma-joined display string
 * (for the Class column) and a parsed tier set (for the data-tier
 * filter attribute).
 *
 * @param  list<int> $ids
 * @return array{0: array<int, string>, 1: array<int, list<string>>}
 *         [classes-by-reach-id, tiers-by-reach-id]
 */
function _load_custom_classes(PDO $db, array $ids): array
{
    $placeholders = implode(',', array_fill(0, count($ids), '?'));
    $cls_stmt = $db->prepare(
        "SELECT reach_id, name FROM reach_class WHERE reach_id IN ($placeholders)"
    );
    $cls_stmt->execute($ids);
    $class_rows_by_reach = [];
    foreach ($cls_stmt->fetchAll() as $row) {
        $class_rows_by_reach[(int)$row['reach_id']][] = $row['name'];
    }
    $classes = [];
    $tiers_by_reach = [];
    $order = array_flip(['I', 'II', 'III', 'IV', 'V']);
    foreach ($class_rows_by_reach as $rid => $names) {
        $classes[$rid] = implode(', ', $names);
        $merged = [];
        foreach ($names as $n) {
            foreach (parse_class_tiers($n) as $t) {
                $merged[$t] = true;
            }
        }
        uksort($merged, fn($a, $b) => $order[$a] <=> $order[$b]);
        $tiers_by_reach[$rid] = array_keys($merged);
    }
    return [$classes, $tiers_by_reach];
}

/**
 * Gauge-id-by-reach-id map + per-gauge sparkline data (last 48h of
 * flow obs, downsampled to ~60 points). Single batched fetch across
 * all source_ids — O(1) queries regardless of gauge count.
 *
 * @param  list<int> $ids
 * @return array{0: array<int, int>, 1: array<int, list<array{ts: int, v: float}>>}
 *         [gauge_map: reach_id → gauge_id; sparklines: gauge_id → points]
 */
function _load_custom_sparklines(PDO $db, array $ids): array
{
    $placeholders = implode(',', array_fill(0, count($ids), '?'));
    $gauge_map = [];
    $gid_stmt = $db->prepare("SELECT id, gauge_id FROM reach WHERE id IN ($placeholders)");
    $gid_stmt->execute($ids);
    foreach ($gid_stmt->fetchAll() as $row) {
        if ($row['gauge_id']) {
            $gauge_map[(int)$row['id']] = (int)$row['gauge_id'];
        }
    }

    $gauge_ids = array_values(array_unique(array_filter(array_values($gauge_map))));
    $sparklines = [];
    if (!$gauge_ids) {
        return [$gauge_map, $sparklines];
    }

    $gph = implode(',', array_fill(0, count($gauge_ids), '?'));
    $src_stmt = $db->prepare(
        "SELECT gauge_id, MIN(source_id) AS source_id FROM gauge_source
         WHERE gauge_id IN ($gph) GROUP BY gauge_id"
    );
    $src_stmt->execute($gauge_ids);
    $gauge_sources = [];
    foreach ($src_stmt->fetchAll() as $row) {
        $gauge_sources[(int)$row['gauge_id']] = (int)$row['source_id'];
    }

    // Single batched fetch across all source_ids — O(1) queries.
    $source_ids = array_values($gauge_sources);
    $sph = implode(',', array_fill(0, count($source_ids), '?'));
    $spark_stmt = $db->prepare(
        "SELECT source_id, value, observed_at FROM observation
         WHERE source_id IN ($sph) AND data_type = 'flow'
           AND observed_at >= datetime('now', '-48 hours')
         ORDER BY source_id, observed_at"
    );
    $spark_stmt->execute($source_ids);
    $by_source = [];
    foreach ($spark_stmt->fetchAll() as $row) {
        $by_source[(int)$row['source_id']][] = [
            'ts' => strtotime($row['observed_at']),
            'v' => (float)$row['value'],
        ];
    }

    foreach ($gauge_sources as $gid => $sid) {
        $all = $by_source[$sid] ?? [];
        // Downsample to ~60 points (every 48min over 48h).
        $n = count($all);
        if ($n > 60) {
            $step = $n / 60;
            $sampled = [];
            for ($i = 0; $i < 60; $i++) {
                $sampled[] = $all[(int)($i * $step)];
            }
            $sampled[] = $all[$n - 1];
            $all = $sampled;
        }
        if (count($all) >= 3) {
            $sparklines[$gid] = $all;
        }
    }
    return [$gauge_map, $sparklines];
}

/**
 * Build the inline SVG sparkline for a single gauge's 48h flow trace.
 * Returns empty string if < 3 points.
 *
 * @param list<array{ts: int, v: float}> $data
 */
function _build_custom_sparkline(array $data, int $w = 80, int $h = 20): string
{
    if (count($data) < 3) {
        return '';
    }
    $xs = array_column($data, 'ts');
    $ys = array_column($data, 'v');
    $x_min = min($xs);
    $x_max = max($xs);
    $y_min = min($ys);
    $y_max = max($ys);
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

/**
 * Compute the union of filter-pill values present across the
 * rendered rows so the UI only offers choices that map to
 * something visible.
 *
 * @param  list<array<string, mixed>>  $reaches
 * @param  array<int, list<string>>    $tiers_by_reach
 * @return array{
 *     states:   array<string, true>,
 *     basins:   array<string, true>,
 *     statuses: list<string>,
 *     tiers:    list<string>
 * }
 */
function _compute_custom_filters(array $reaches, array $tiers_by_reach): array
{
    $states_present = [];
    $basins_present = [];
    $statuses_present = [];
    $tiers_present = [];
    foreach ($reaches as $s) {
        $rid = (int)$s['id'];
        if (!empty($s['state'])) {
            $states_present[$s['state']] = true;
        }
        $basins_present[$s['drainage'] ?? ''] = true;
        $statuses_present[$s['status'] ?? 'unknown'] = true;
        foreach ($tiers_by_reach[$rid] ?? ['?'] as $t) {
            $tiers_present[$t] = true;
        }
        if (empty($tiers_by_reach[$rid])) {
            $tiers_present['?'] = true;
        }
    }
    ksort($states_present);
    ksort($basins_present);
    $status_order = ['low', 'okay', 'high', 'unknown'];
    $statuses_sorted = array_values(array_filter($status_order, fn($s) => isset($statuses_present[$s])));
    $tier_order = ['I', 'II', 'III', 'IV', 'V', '?'];
    $tiers_sorted = array_values(array_filter($tier_order, fn($t) => isset($tiers_present[$t])));
    return [
        'states'   => $states_present,
        'basins'   => $basins_present,
        'statuses' => $statuses_sorted,
        'tiers'    => $tiers_sorted,
    ];
}

/**
 * Page header — h2 + edit/home links + reach count + filter-bar
 * (state/watershed/status/class pills, only including values that
 * appear on the page).
 *
 * @param list<array<string, mixed>>  $reaches
 * @param array<int, list<string>>    $tiers_by_reach
 */
function _render_custom_header(array $reaches, array $tiers_by_reach): void
{
    $filters = _compute_custom_filters($reaches, $tiers_by_reach);
    $states_present = $filters['states'];
    $basins_present = $filters['basins'];
    $statuses_sorted = $filters['statuses'];
    $tiers_sorted = $filters['tiers'];
    $status_meta = CUSTOM_LEVELS_STATUS_META;
    $fg_toggle = '<span class="fg-toggle">'
        . '<button type="button" data-all>All</button>'
        . '<button type="button" data-none>None</button>'
        . '</span>';
    $reach_count = count($reaches);
    // "Edit selection" carries the resolved reach ids so picker.php
    // pre-checks them — picker.js:readIdsFromUrl() does the auto-check
    // when ?ids= is present. Rebuild from $reaches rather than threading
    // the original $ids through _render_custom_header's signature; that
    // naturally drops any ids the DB couldn't resolve (which the picker
    // wouldn't pre-check anyway). Per docs/done/PLAN_map_and_ui_tweaks.md Item 3.
    $id_csv = implode(',', array_map(static fn(array $r): int => (int)$r['id'], $reaches));
    $picker_href = '/picker.php' . ($id_csv !== '' ? '?ids=' . $id_csv : '');
    ?>
<h2>Custom Levels Page</h2>
<p style="margin:.3rem 0 .5rem;font-size:.85rem">
  <a href="<?= htmlspecialchars($picker_href, ENT_QUOTES) ?>">Edit selection</a> | <a href="/index.html">Home</a>
  | <?= $reach_count ?> reach<?= $reach_count !== 1 ? 'es' : '' ?>
</p>

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
  <details class="filter-group">
    <summary>Watershed <span class="fg-count"><?= count($basins_present) ?></span></summary>
    <div class="filter-pills" data-group="basin">
      <?= $fg_toggle ?>
<?php foreach (array_keys($basins_present) as $b): $disp = $b === '' ? '(none)' : $b; ?>
      <label><input type="checkbox" value="<?= htmlspecialchars($b) ?>" checked><?= htmlspecialchars($disp) ?></label>
<?php endforeach; ?>
    </div>
  </details>
  <details class="filter-group">
    <summary>Status <span class="fg-count"><?= count($statuses_sorted) ?></span></summary>
    <div class="filter-pills" data-group="status">
      <?= $fg_toggle ?>
<?php foreach ($statuses_sorted as $st): $m = $status_meta[$st]; ?>
      <label><input type="checkbox" value="<?= htmlspecialchars($st) ?>" checked><span class="swatch" style="background:<?= $m['swatch'] ?>"></span><?= htmlspecialchars($m['label']) ?></label>
<?php endforeach; ?>
    </div>
  </details>
  <details class="filter-group">
    <summary>Class <span class="fg-count"><?= count($tiers_sorted) ?></span></summary>
    <div class="filter-pills" data-group="tier" data-split="csv">
      <?= $fg_toggle ?>
<?php foreach ($tiers_sorted as $t): ?>
      <label><input type="checkbox" value="<?= htmlspecialchars($t) ?>" checked><?= htmlspecialchars($t) ?></label>
<?php endforeach; ?>
    </div>
  </details>
  <div class="filter-meta" aria-live="polite">
    <span class="fb-count"></span>
    <button type="button" class="fb-reset">Reset</button>
  </div>
</div>
    <?php
}

/**
 * The 9-column levels table — Status / Name / Location / Date /
 * Flow / Height / Temp / Watershed / Class. Each row carries
 * data-{state,basin,status,tier} attrs for client-side filtering.
 *
 * @param list<array<string, mixed>>                       $reaches
 * @param array<int, list<string>>                         $tiers_by_reach
 * @param array<int, string>                               $classes
 * @param array<int, list<array{ts: int, v: float}>>       $sparklines
 * @param array<int, int>                                  $gauge_map
 */
function _render_custom_table(
    array $reaches,
    array $tiers_by_reach,
    array $classes,
    array $sparklines,
    array $gauge_map,
): void {
    ?>
<table class="levels">
<thead><tr>
  <th>Status</th>
  <th>Name</th>
  <th>Location</th>
  <th>Date</th>
  <th><a href="#Units">Flow<br>CFS</a></th>
  <th><a href="#Units">Height<br>Feet</a></th>
  <th><a href="#Units">Temp<br>F</a></th>
  <th class="secondary">Watershed</th>
  <th class="secondary">Class</th>
</tr></thead>
<tbody>
<?php foreach ($reaches as $s):
    $id = (int)$s['id'];

    // Status from flow delta.
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

    // Best available timestamp — render as <time> for client-side
    // local conversion.
    $time_html = '';
    $ts = $s['flow_time'] ?? $s['gage_time'] ?? $s['temp_time'] ?? null;
    if ($ts) {
        $iso = gmdate('Y-m-d\TH:i:s\Z', strtotime($ts));
        $display = date('m/d H:i', strtotime($ts));
        $time_html = "<time datetime=\"$iso\">$display</time>";
    }

    $name = htmlspecialchars($s['display_name'] ?? '');
    $loc  = htmlspecialchars($s['gauge_location'] ?? '');
    $spark = '';
    $gid = $gauge_map[$id] ?? null;
    if ($gid && isset($sparklines[$gid])) {
        $spark = _build_custom_sparkline($sparklines[$gid]);
    }

    $flow_val = $s['flow'] !== null ? number_format((float)$s['flow'], 0) : '';
    $flow = $flow_val !== '' ? '<a href="/plot.php?type=flow&id=' . $id . '">' . $flow_val . '</a>' . $spark : '';
    $gage = $s['gage'] !== null ? '<a href="/plot.php?type=gage&id=' . $id . '">' . number_format((float)$s['gage'], 2) . '</a>' : '';
    $temp = $s['temperature'] !== null ? '<a href="/plot.php?type=temp&id=' . $id . '">' . number_format((float)$s['temperature'], 0) . '</a>' : '';
    $drain = htmlspecialchars($s['drainage'] ?? '');
    $class = htmlspecialchars($classes[$id] ?? '');

    $row_tiers = $tiers_by_reach[$id] ?? [];
    $tier_attr = $row_tiers ? implode(',', $row_tiers) : '?';
    $state_attr = htmlspecialchars($s['state'] ?? '');
    $basin_attr = htmlspecialchars($s['drainage'] ?? '');
    $status_attr = htmlspecialchars($s['status'] ?? 'unknown');
?>
<tr class="clickable-row" data-href="/description.php?id=<?= $id ?>"
    data-state="<?= $state_attr ?>"
    data-basin="<?= $basin_attr ?>"
    data-status="<?= $status_attr ?>"
    data-tier="<?= htmlspecialchars($tier_attr) ?>">
  <td class="td-status" data-label="Status"><?= $status ?></td>
  <td class="td-name" data-label="Name"><a href="/description.php?id=<?= $id ?>"><?= $name ?></a></td>
  <td data-label="Location"><?= $loc ?></td>
  <td class="td-date" data-label="Date"><?= $time_html ?></td>
  <td class="td-flow" data-label="Flow"><?= $flow ?></td>
  <td class="td-gage" data-label="Height"><?= $gage ?></td>
  <td class="td-temp" data-label="Temp"><?= $temp ?></td>
  <td class="secondary" data-label="Watershed"><?= $drain ?></td>
  <td class="secondary" data-label="Class"><?= $class ?></td>
</tr>
<?php endforeach; ?>
</tbody>
</table>
    <?php
}

/**
 * Units note + cache-busting deferred-load of filters.js.
 */
function _render_custom_footer(): void
{
    ?>
<p id="Units" style="margin-top:.5rem;font-size:.75rem;color:#888">
  CFS = cubic feet per second. Feet = gage height in feet. F = Fahrenheit.
</p>
    <?php
    $filters_mtime = @filemtime($_SERVER['DOCUMENT_ROOT'] . '/static/filters.js') ?: 1;
    ?>
<script src="/static/filters.js?v=<?= $filters_mtime ?>" defer></script>
    <?php
}
