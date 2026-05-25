<?php
declare(strict_types=1);
/**
 * Gauge Picker — "Build Your Own Gauges Page"
 *
 * HTML page: state pills + watershed (HUC6/8) filter + search + gauge checklist.
 * AJAX endpoint (?ajax=1&states=Oregon,Washington): returns JSON gauges.
 *
 * Mirrors picker.php (which builds custom reach pages); this one builds
 * /custom_gauges.php?ids=1,2,3 URLs.
 */
require_once __DIR__ . '/includes/db.php';

$STATE_ABBREVS = [
    'AZ' => 'Arizona',  'CA' => 'California', 'CO' => 'Colorado',
    'ID' => 'Idaho',    'KS' => 'Kansas',     'MT' => 'Montana',
    'NV' => 'Nevada',   'NM' => 'New Mexico', 'OR' => 'Oregon',
    'UT' => 'Utah',     'WA' => 'Washington', 'WY' => 'Wyoming',
];
$ABBREV_TO_STATE = $STATE_ABBREVS;
$STATE_TO_ABBREV = array_flip($STATE_ABBREVS);

$db = get_db();

// -----------------------------------------------------------------------
// AJAX JSON endpoint — one row per gauge for client-side filter/render.
// -----------------------------------------------------------------------
if (filter_input(INPUT_GET, 'ajax', FILTER_VALIDATE_INT)) {
    header('Content-Type: application/json');
    header('Cache-Control: max-age=60');

    $raw = (string)(filter_input(INPUT_GET, 'states', FILTER_DEFAULT) ?? '');
    $state_names = array_filter(array_map('trim', explode(',', $raw)));
    if (!$state_names) {
        echo '[]';
        exit;
    }

    // Translate full names → postal abbreviations (gauge.state stores 'OR').
    $abbrevs = array_values(array_filter(array_map(
        fn($n) => $STATE_TO_ABBREV[$n] ?? null,
        $state_names
    )));
    if (!$abbrevs) {
        echo '[]';
        exit;
    }

    $placeholders = implode(',', array_fill(0, count($abbrevs), '?'));

    // Only gauges that have at least one current observation.
    $sql = <<<SQL
SELECT g.id,
       COALESCE(g.river,    g.name) AS river,
       COALESCE(g.location, '')     AS location,
       g.state                      AS state_abbrev,
       SUBSTR(g.huc, 1, 8)          AS huc8,
       g.sort_name                  AS sort_name,
       lo_flow.value                AS flow,
       lo_flow.delta_per_hour       AS flow_delta,
       lo_gage.value                AS gage
FROM gauge g
LEFT JOIN latest_gauge_observation lo_flow
       ON g.id = lo_flow.gauge_id AND lo_flow.data_type = 'flow'
LEFT JOIN latest_gauge_observation lo_gage
       ON g.id = lo_gage.gauge_id AND lo_gage.data_type = 'gauge'
WHERE g.state IN ($placeholders)
  AND g.id IN (SELECT DISTINCT gauge_id FROM latest_gauge_observation)
ORDER BY g.sort_name
SQL;

    $stmt = $db->prepare($sql);
    $stmt->execute($abbrevs);
    $rows = $stmt->fetchAll();

    // Map abbrev back to full name so the row's data-state matches the
    // pill values that filters.js compares against.
    foreach ($rows as &$row) {
        $abbrev = (string)($row['state_abbrev'] ?? '');
        $row['state'] = $ABBREV_TO_STATE[$abbrev] ?? '';
        $row['huc8'] = $row['huc8'] ?? '';
    }
    unset($row);

    echo json_encode($rows, JSON_UNESCAPED_UNICODE);
    exit;
}

// -----------------------------------------------------------------------
// HTML page
// -----------------------------------------------------------------------
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

// All states that have at least one runnable gauge with current data.
$state_rows = db_query($db,
    "SELECT DISTINCT state FROM gauge
     WHERE state IS NOT NULL
       AND id IN (SELECT DISTINCT gauge_id FROM latest_gauge_observation)
     ORDER BY state"
)->fetchAll();
$all_states = [];
foreach ($state_rows as $r) {
    $name = $ABBREV_TO_STATE[$r['state']] ?? null;
    if ($name) $all_states[] = $name;
}
sort($all_states);

// All HUC8s (with HUC6 parent) for the watershed filter — built once across
// every state so toggling state pills doesn't rebuild the filter UI.
$huc_rows = db_query($db,
    "SELECT DISTINCT SUBSTR(g.huc, 1, 8) AS huc8, SUBSTR(g.huc, 1, 6) AS huc6
     FROM gauge g
     WHERE g.huc IS NOT NULL AND LENGTH(g.huc) >= 8
       AND g.id IN (SELECT DISTINCT gauge_id FROM latest_gauge_observation)"
)->fetchAll();
// PHP coerces all-digit array keys to int; cast back to string when reading.
$huc8_codes = [];
$huc6_to_huc8s = [];
foreach ($huc_rows as $r) {
    $huc8_codes[(string)$r['huc8']] = true;
    $huc6_to_huc8s[(string)$r['huc6']][(string)$r['huc8']] = true;
}
$huc8_names = [];
$huc6_names = [];
if ($huc8_codes) {
    $codes = array_map('strval', array_keys($huc8_codes));
    $hp = implode(',', array_fill(0, count($codes), '?'));
    $hn8 = $db->prepare("SELECT code, name FROM huc_name WHERE level = 8 AND code IN ($hp)");
    $hn8->execute($codes);
    foreach ($hn8->fetchAll() as $r) $huc8_names[(string)$r['code']] = $r['name'];

    $huc6 = array_map('strval', array_keys($huc6_to_huc8s));
    $hp6 = implode(',', array_fill(0, count($huc6), '?'));
    $hn6 = $db->prepare("SELECT code, name FROM huc_name WHERE level = 6 AND code IN ($hp6)");
    $hn6->execute($huc6);
    foreach ($hn6->fetchAll() as $r) $huc6_names[(string)$r['code']] = $r['name'];
}
uksort($huc6_to_huc8s, fn($a, $b) => strcmp(
    $huc6_names[(string)$a] ?? (string)$a,
    $huc6_names[(string)$b] ?? (string)$b
));

// Optional ?state=<full name> — pre-checks only that pill so users arriving
// from gauges.<state>.html land focused on their current state. Falls back
// to "all checked" when missing, empty, or not in $all_states.
$state_param = filter_input(INPUT_GET, 'state', FILTER_DEFAULT);
$initial_state = (is_string($state_param) && in_array($state_param, $all_states, true))
    ? $state_param
    : null;

include_header('Build Your Own Gauges Page', 'picker', '', '', ['picker_kind' => 'gauge']);
?>
<h2>Build Your Own Gauges Page</h2>
<p style="margin:.5rem 0;font-size:.85rem">Pick gauges, then view a custom gauges page you can bookmark and share.</p>

<div class="picker-states" id="state-pills">
<?php foreach ($all_states as $name):
  $checked = ($initial_state === null || $name === $initial_state) ? ' checked' : ''; ?>
  <label><input type="checkbox" value="<?= htmlspecialchars($name) ?>"<?= $checked ?>><span><?= htmlspecialchars($name) ?></span></label>
<?php endforeach; ?>
</div>

<?php
$fg_toggle = '<span class="fg-toggle">'
           . '<button type="button" data-all>All</button>'
           . '<button type="button" data-none>None</button>'
           . '</span>';
$total_huc8 = array_sum(array_map('count', $huc6_to_huc8s));
?>
<div class="filter-bar" id="filter-bar" hidden>
  <details class="filter-group" open>
    <summary>Watershed <span class="fg-count"><?= $total_huc8 ?></span></summary>
    <div class="filter-pills" data-group="huc8">
      <?= $fg_toggle ?>
<?php foreach ($huc6_to_huc8s as $h6 => $children):
      $h6 = (string)$h6;
      $h6_name = $huc6_names[$h6] ?? $h6;
      $codes = array_map('strval', array_keys($children));
      sort($codes); ?>
      <details class="filter-subgroup">
        <summary><label class="huc6-parent"><input type="checkbox" data-huc6="<?= htmlspecialchars($h6) ?>" checked><?= htmlspecialchars($h6_name) ?></label> <span class="fg-count"><?= count($codes) ?></span></summary>
        <div class="filter-pills-sub">
<?php foreach ($codes as $h8): $h8_name = $huc8_names[$h8] ?? $h8; ?>
          <label><input type="checkbox" value="<?= htmlspecialchars($h8) ?>" checked><?= htmlspecialchars($h8_name) ?></label>
<?php endforeach; ?>
        </div>
      </details>
<?php endforeach; ?>
    </div>
  </details>
  <div class="filter-meta" aria-live="polite">
    <span class="fb-count"></span>
    <button type="button" class="fb-reset">Reset</button>
  </div>
</div>

<label for="search" class="sr-only">Filter gauges by river name</label>
<input type="text" class="picker-search" id="search" placeholder="Filter gauges by river or location…">

<table class="picker-sections" id="sections">
  <thead>
    <tr>
      <th style="width:1%"><label><input type="checkbox" id="select-all"><span class="sr-only"> Select all</span></label></th>
      <th>River</th>
      <th class="col-location">Location</th>
      <th class="col-flow">Flow</th>
      <th class="col-gage">Gage</th>
    </tr>
  </thead>
  <tbody id="tbody"></tbody>
</table>

<section class="selected-section" aria-labelledby="selected-heading">
  <h3 id="selected-heading" class="selected-heading">
    Selected (display order)
    <button type="button" id="clear-btn" class="clear-btn">Clear</button>
  </h3>
  <p class="selected-hint">
    Drag the <span class="hint-handle" aria-hidden="true">☰</span> handle to reorder.
    Tap <span class="hint-x" aria-hidden="true">✕</span> to remove.
    Keyboard: focus an item then Up/Down arrows to move, Delete to remove.
  </p>
  <ol id="selected-list" class="selected-list" aria-live="polite"></ol>
</section>

<div class="picker-actions" id="actions" style="display:none">
  <span class="count" id="count">0 selected</span>
  <button type="button" id="edit-order-btn" class="edit-order-btn">Edit order ↑</button>
  <a id="view-link" class="disabled" href="#">View Custom Page</a>
  <button id="copy-btn" type="button">Copy Link</button>
  <span class="copied" id="copied" style="display:none">Copied!</span>
</div>

<?php
$filters_mtime = @filemtime($_SERVER['DOCUMENT_ROOT'] . '/static/filters.js') ?: 1;
$picker_mtime  = @filemtime($_SERVER['DOCUMENT_ROOT'] . '/static/gauge_picker.js') ?: 1;
?>
<script src="/static/filters.js?v=<?= $filters_mtime ?>" defer></script>
<script src="/static/gauge_picker.js?v=<?= $picker_mtime ?>" defer></script>
<?php
include_footer();
