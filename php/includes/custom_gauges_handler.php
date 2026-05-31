<?php

declare(strict_types=1);

/**
 * Handler for /custom_gauges.php — renders a gauges table for arbitrary
 * gauge IDs supplied via `?ids=CSV`. Sister page to custom.php but at
 * the gauge level (no reach-class filter, no flow-status-from-class).
 *
 * Called from custom_gauges.php after arg-parse + empty-redirect.
 * Loads gauge metadata + latest observations + per-gauge status
 * rollup (aggregated across the gauge's reaches), then renders the
 * filterable gauges table.
 *
 * Helpers prefixed with `_` are file-private and carry the file's
 * name as part of the prefix (Tier 5 CI-lesson note in
 * docs/done/PLAN_php_layer_split.md).
 */

require_once __DIR__ . '/db.php';
require_once __DIR__ . '/pubhash_request.php';
require_once __DIR__ . '/header.php';
require_once __DIR__ . '/footer.php';

/** Two-letter abbrev → display name for the State filter pill. */
const CUSTOM_GAUGES_STATE_ABBREVS = [
    'AZ' => 'Arizona',  'CA' => 'California', 'CO' => 'Colorado',
    'ID' => 'Idaho',    'KS' => 'Kansas',     'MT' => 'Montana',
    'NV' => 'Nevada',   'NM' => 'New Mexico', 'OR' => 'Oregon',
    'UT' => 'Utah',     'WA' => 'Washington', 'WY' => 'Wyoming',
];

/**
 * Dispatch and write the full HTTP response.
 *
 * @param list<int> $ids     Already-validated, > 0, capped at 200.
 * @param string    $id_param Raw `ids` query param (for the edit-selection link).
 */
function handle_custom_gauges(PDO $db, array $ids, string $id_param): void
{
    $gauges_by_id = _load_custom_gauges_rows($db, $ids);
    $status_by_gauge = _load_custom_gauges_status_rollup($db, $ids);
    $rows = _custom_gauges_reorder_by_position($gauges_by_id, $ids);

    $filters = _compute_custom_gauges_filters($rows);
    [$huc8_names, $huc6_names, $huc6_groups] = _load_custom_gauges_huc_groups(
        $db,
        array_keys($filters['huc8_present']),
    );

    header('Cache-Control: max-age=60');
    include_header('Custom Gauges Page', '', '', '', ['picker_kind' => 'gauge']);

    _render_custom_gauges_header(
        count($rows),
        $filters['states'],
        $huc6_groups,
        $huc6_names,
        $filters['has_no_huc'],
        $id_param,
    );
    _render_custom_gauges_table($rows, $status_by_gauge);
    _render_custom_gauges_footer();

    include_footer();
}

/**
 * Gauge metadata + latest observations (flow, inflow, gauge height,
 * temperature) for every requested gauge. Returns keyed by gauge id
 * so the URL-order reorder is a cheap O(N) lookup.
 *
 * Shapes verified against the dev DB: g.id is the PK (int); river and
 * location are COALESCE(...) so non-null strings; state/huc are
 * nullable text; every lo_*.value is a LEFT-JOIN real (float|null) and
 * lo_*.observed_at a LEFT-JOIN text (string|null).
 *
 * @param  list<int> $ids
 * @return array<int, array{id: int, river: string, location: string, state_abbrev: string|null, huc: string|null, flow: float|null, flow_time: string|null, inflow: float|null, inflow_time: string|null, gage: float|null, gage_time: string|null, temperature: float|null, temp_time: string|null}>
 */
function _load_custom_gauges_rows(PDO $db, array $ids): array
{
    $placeholders = implode(',', array_fill(0, count($ids), '?'));
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
    /** @var list<array{id: int, river: string, location: string, state_abbrev: string|null, huc: string|null, flow: float|null, flow_time: string|null, inflow: float|null, inflow_time: string|null, gage: float|null, gage_time: string|null, temperature: float|null, temp_time: string|null}> $fetched */
    $fetched = $stmt->fetchAll();
    foreach ($fetched as $row) {
        $gauges_by_id[$row['id']] = $row;
    }
    return $gauges_by_id;
}

/**
 * Per-gauge status rollup — across each gauge's reaches, count
 * low/okay/high based on the reach_class flow range vs the gauge's
 * current flow. The representative label is 'okay' if any reach is
 * okay, else whichever of low/high has the higher count (ties favor
 * low), else null. Matches the levels build's gauges.html logic.
 *
 * @param  list<int> $ids
 * @return array<int, array{label: ?string, counts: array<string, int>}>
 */
function _load_custom_gauges_status_rollup(PDO $db, array $ids): array
{
    $placeholders = implode(',', array_fill(0, count($ids), '?'));
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
    $stmt = $db->prepare($status_sql);
    $stmt->execute($ids);
    $status_by_gauge = [];
    foreach ($stmt->fetchAll() as $r) {
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
            'counts' => array_filter(['low' => $n_low, 'okay' => $n_okay, 'high' => $n_high], fn($n) => $n > 0),
        ];
    }
    return $status_by_gauge;
}

/**
 * Project keyed gauge rows into URL-supplied position order, dropping
 * IDs that didn't match a row (defensive — the WHERE r.id IN (…)
 * should make that impossible).
 *
 * @param  array<int, array{id: int, river: string, location: string, state_abbrev: string|null, huc: string|null, flow: float|null, flow_time: string|null, inflow: float|null, inflow_time: string|null, gage: float|null, gage_time: string|null, temperature: float|null, temp_time: string|null}> $gauges_by_id
 * @param  list<int>                  $ids
 * @return list<array{id: int, river: string, location: string, state_abbrev: string|null, huc: string|null, flow: float|null, flow_time: string|null, inflow: float|null, inflow_time: string|null, gage: float|null, gage_time: string|null, temperature: float|null, temp_time: string|null}>
 */
function _custom_gauges_reorder_by_position(array $gauges_by_id, array $ids): array
{
    $rows = [];
    foreach ($ids as $id) {
        if (isset($gauges_by_id[$id])) {
            $rows[] = $gauges_by_id[$id];
        }
    }
    return $rows;
}

/**
 * Collect the union of state + huc8 values present on the page so the
 * filter pills only offer values that map to something visible.
 *
 * @param  list<array{state_abbrev: string|null, huc: string|null}> $rows
 * @return array{
 *     states:       array<string, true>,
 *     huc8_present: array<string, true>,
 *     has_no_huc:   bool
 * }
 */
function _compute_custom_gauges_filters(array $rows): array
{
    $states_present = [];
    $huc8_present   = [];
    $has_no_huc     = false;
    foreach ($rows as $r) {
        // gauge.state may be a comma list ('OR,WA') for a border gauge; split
        // it so each state contributes a pill, mirroring the static build.
        foreach (explode(',', $r['state_abbrev'] ?? '') as $abbrev) {
            $abbrev = trim($abbrev);
            if ($abbrev !== '' && isset(CUSTOM_GAUGES_STATE_ABBREVS[$abbrev])) {
                $states_present[CUSTOM_GAUGES_STATE_ABBREVS[$abbrev]] = true;
            }
        }
        $huc = $r['huc'] ?? '';
        if (strlen($huc) >= 8) {
            $huc8_present[substr($huc, 0, 8)] = true;
        } else {
            $has_no_huc = true;
        }
    }
    ksort($states_present);
    return [
        'states'       => $states_present,
        'huc8_present' => $huc8_present,
        'has_no_huc'   => $has_no_huc,
    ];
}

/**
 * Look up HUC8 + HUC6 display names and group the HUC8s under their
 * HUC6 parent, sorted by parent name. PHP coerces all-digit array
 * keys to int, so the caller MUST pass string codes (and we cast
 * back to string at every read site).
 *
 * @param  list<string|int> $huc8_codes  May arrive int-keyed; coerced internally.
 * @return array{
 *     0: array<string, string>,
 *     1: array<string, string>,
 *     2: array<string, list<array{0: string, 1: string}>>
 * }   [huc8_names, huc6_names, huc6_groups]
 */
function _load_custom_gauges_huc_groups(PDO $db, array $huc8_codes): array
{
    $huc8_names = [];
    $huc6_names = [];
    $huc6_groups = [];
    if ($huc8_codes === []) {
        return [$huc8_names, $huc6_names, $huc6_groups];
    }
    $huc8_codes = array_map('strval', $huc8_codes);

    $hp = implode(',', array_fill(0, count($huc8_codes), '?'));
    $hn8 = $db->prepare("SELECT code, name FROM huc_name WHERE level = 8 AND code IN ($hp)");
    $hn8->execute($huc8_codes);
    foreach ($hn8->fetchAll() as $r) {
        $huc8_names[(string)$r['code']] = $r['name'];
    }

    $huc6_codes = array_values(array_unique(array_map(fn($c) => substr($c, 0, 6), $huc8_codes)));
    $hp6 = implode(',', array_fill(0, count($huc6_codes), '?'));
    $hn6 = $db->prepare("SELECT code, name FROM huc_name WHERE level = 6 AND code IN ($hp6)");
    $hn6->execute($huc6_codes);
    foreach ($hn6->fetchAll() as $r) {
        $huc6_names[(string)$r['code']] = $r['name'];
    }

    foreach ($huc8_codes as $h8) {
        $h6 = substr($h8, 0, 6);
        $huc6_groups[$h6][] = [$h8, $huc8_names[$h8] ?? $h8];
    }
    uksort($huc6_groups, fn($a, $b) => strcmp(
        $huc6_names[$a] ?? strval($a),
        $huc6_names[$b] ?? strval($b),
    ));
    foreach ($huc6_groups as &$arr) {
        sort($arr);
    }
    unset($arr);

    return [$huc8_names, $huc6_names, $huc6_groups];
}

/**
 * Page header — h2 + nav links + gauge count + filter-bar
 * (state + nested huc6/huc8 watershed pills, only including values
 * that appear on the page).
 *
 * @param array<string, true>                                $states_present
 * @param array<string, list<array{0: string, 1: string}>>   $huc6_groups
 * @param array<string, string>                              $huc6_names
 */
function _render_custom_gauges_header(
    int $row_count,
    array $states_present,
    array $huc6_groups,
    array $huc6_names,
    bool $has_no_huc,
    string $id_param,
): void {
    $id_param_safe = htmlspecialchars($id_param);
    $fg_toggle = '<span class="fg-toggle">'
        . '<button type="button" data-all>All</button>'
        . '<button type="button" data-none>None</button>'
        . '</span>';
    ?>
<h2>Custom Gauges Page</h2>
<p style="margin:.3rem 0 .5rem;font-size:.85rem">
  <a href="/gauge_picker.php?ids=<?= $id_param_safe ?>">Edit selection</a> |
  <a href="/gauges.html">All gauges</a> |
  <a href="/index.html">Home</a>
  | <?= $row_count ?> gauge<?= $row_count !== 1 ? 's' : '' ?>
</p>

<div class="filter-bar" id="filter-bar" hidden>
<?php if (count($states_present) > 1): ?>
  <details class="filter-group">
    <summary>State <span class="fg-count"><?= count($states_present) ?></span></summary>
    <div class="filter-pills" data-group="state" data-split="csv">
      <?= $fg_toggle ?>
<?php foreach (array_keys($states_present) as $st): ?>
      <label><input type="checkbox" value="<?= htmlspecialchars($st) ?>" checked><?= htmlspecialchars($st) ?></label>
<?php endforeach; ?>
    </div>
  </details>
<?php endif; ?>
<?php if ($huc6_groups !== [] || $has_no_huc):
    $total = array_sum(array_map('count', $huc6_groups)) + ($has_no_huc ? 1 : 0); ?>
  <details class="filter-group" open>
    <summary>Watershed <span class="fg-count"><?= $total ?></span></summary>
    <div class="filter-pills" data-group="huc8">
      <?= $fg_toggle ?>
<?php foreach ($huc6_groups as $h6 => $children):
        $h6 = strval($h6);  // numeric HUC keys are coerced to int by PHP; strict_types needs string
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
    <?php
}

/**
 * The 8-column gauges table — Status / River / Location / Date /
 * Flow / 2-day Trend (sparkline placeholder) / Gauge / Temp. The
 * sparkline placeholder is lazy-loaded by levels.js from
 * /static/sparklines.json, matching gauges.html.
 *
 * @param list<array{id: int, river: string, location: string, state_abbrev: string|null, huc: string|null, flow: float|null, flow_time: string|null, inflow: float|null, inflow_time: string|null, gage: float|null, gage_time: string|null, temperature: float|null, temp_time: string|null}> $rows
 * @param array<int, array{label: ?string, counts: array<string, int>}>  $status_by_gauge
 */
function _render_custom_gauges_table(array $rows, array $status_by_gauge): void
{
    ?>
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
    $gid = $r['id'];
    // A border gauge's state_abbrev is a comma list ('OR,WA'); map each abbrev
    // to its full name and re-join ('Oregon,Washington'). The State filter
    // group is rendered data-split="csv", so filters.js splits this data-state
    // to match each pill.
    $state_names = [];
    foreach (explode(',', $r['state_abbrev'] ?? '') as $abbrev) {
        $abbrev = trim($abbrev);
        if (isset(CUSTOM_GAUGES_STATE_ABBREVS[$abbrev])) {
            $state_names[] = CUSTOM_GAUGES_STATE_ABBREVS[$abbrev];
        }
    }
    $state = implode(',', $state_names);
    $huc_str = $r['huc'] ?? '';
    $huc8 = strlen($huc_str) >= 8 ? substr($huc_str, 0, 8) : '';

    // Status cell — rollup label + per-bucket count tooltip.
    $status_info = $status_by_gauge[$gid] ?? null;
    $status_word = $status_info['label'] ?? null;
    if ($status_word !== null) {
        $counts = $status_info['counts'] ?? [];
        $count_summary = implode(', ', array_map(fn($k, $v) => "$v $k", array_keys($counts), array_values($counts)));
        $title = $count_summary !== '' ? ' title="' . htmlspecialchars($count_summary) . '"' : '';
        $status_cell = '<span class="level-' . htmlspecialchars($status_word) . '"' . $title . '>' . htmlspecialchars($status_word) . '</span>';
    } else {
        $status_cell = '';
    }

    // Best-available time — latest of the four observation timestamps.
    $times = array_filter([$r['flow_time'], $r['inflow_time'], $r['gage_time'], $r['temp_time']], fn($t) => $t !== null);
    $time_html = '';
    if ($times !== []) {
        // strtotime returns int|false; DB timestamps always parse, but cast
        // each result to int (matching description_detail/gauge_detail) so
        // $latest is a clean int for gmdate.
        $latest = max(array_map(fn(string $t): int => (int)strtotime($t), $times));
        $iso = gmdate('Y-m-d\TH:i:s\Z', $latest);
        $disp = gmdate('m/d H:i', $latest);
        $time_html = "<time datetime=\"$iso\">$disp</time>";
    }

    // Flow cell — prefer flow, fall back to inflow, then gage (as feet),
    // matching gauges.html _build_gauges_table.
    $flow_val = $r['flow'] ?? $r['inflow'];
    $gage_val = $r['gage'];
    if ($flow_val !== null) {
        $flow_cell = number_format($flow_val, 0);
    } elseif ($gage_val !== null) {
        $flow_cell = number_format($gage_val, 1) . '&prime;';
    } else {
        $flow_cell = '';
    }

    $gage_cell = $gage_val !== null ? number_format($gage_val, 1) : '';
    $temp_val = $r['temperature'];
    $temp_cell = $temp_val !== null ? number_format($temp_val, 1) : '';

    $attrs = '';
    if ($state !== '' && $huc8 !== '') {
        $attrs = ' data-state="' . htmlspecialchars($state) . '" data-huc8="' . htmlspecialchars($huc8) . '"';
    }
    $status_attr = $status_word !== null ? ' data-status="' . htmlspecialchars($status_word) . '"' : '';
?>
<tr class="clickable-row" data-href="/gauge.php?h=<?= pubhash_encode($gid) ?>"<?= $attrs ?><?= $status_attr ?>>
  <td class="td-status" data-label="Status"><?= $status_cell ?></td>
  <td class="td-name" data-label="River"><a href="/gauge.php?h=<?= pubhash_encode($gid) ?>"><?= htmlspecialchars($r['river']) ?></a></td>
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
}

/**
 * Cache-busting deferred-load of filters.js (no units note — gauges
 * page header already explains the units in the column titles).
 */
function _render_custom_gauges_footer(): void
{
    $doc_root = is_string($_SERVER['DOCUMENT_ROOT'] ?? null) ? $_SERVER['DOCUMENT_ROOT'] : '';
    $filters_mtime_raw = @filemtime($doc_root . '/static/filters.js');
    $filters_mtime = $filters_mtime_raw !== false ? $filters_mtime_raw : 1;
    ?>
<script src="/static/filters.js?v=<?= $filters_mtime ?>" defer></script>
    <?php
}
