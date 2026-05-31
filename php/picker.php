<?php
declare(strict_types=1);
/**
 * Picker — "Build Your Own Levels Page"
 *
 * HTML page: state / basin / status / class filters + text search + reach checklist.
 * AJAX endpoint (?ajax=1&states=Washington,Oregon): returns JSON reaches.
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/class_tiers.php';
require_once __DIR__ . '/includes/pubhash_request.php';

$db = get_db();

// -----------------------------------------------------------------------
// AJAX JSON endpoint — returns one row per reach with enough metadata
// for client-side filtering (state/basin/status/tiers) and display.
// -----------------------------------------------------------------------
$ajax = filter_input(INPUT_GET, 'ajax', FILTER_VALIDATE_INT);
if (is_int($ajax) && $ajax !== 0) {
    header('Content-Type: application/json');
    header('Cache-Control: max-age=60');

    $raw = (string)(filter_input(INPUT_GET, 'states', FILTER_DEFAULT) ?? '');
    $state_names = array_filter(array_map('trim', explode(',', $raw)), fn($s) => $s !== '');
    if ($state_names === []) {
        echo '[]';
        exit;
    }

    $placeholders = implode(',', array_fill(0, count($state_names), '?'));

    $sql = <<<SQL
SELECT DISTINCT r.id,
       COALESCE(r.display_name, r.name) AS name,
       r.sort_name,
       r.basin                        AS basin,
       COALESCE(NULLIF(r.description, ''), g.location) AS location,
       lo_flow.value                  AS flow,
       lo_gage.value                  AS gage,
       lo_flow.delta_per_hour         AS flow_delta,
       CASE
           WHEN rc_range.low IS NULL OR lo_flow.value IS NULL THEN NULL
           WHEN lo_flow.value <  rc_range.low  THEN 'low'
           WHEN lo_flow.value >  rc_range.high THEN 'high'
           ELSE 'okay'
       END                            AS status
FROM reach r
JOIN reach_state rs ON r.id = rs.reach_id
JOIN state st ON rs.state_id = st.id
LEFT JOIN gauge g ON r.gauge_id = g.id
LEFT JOIN latest_gauge_observation lo_flow
       ON g.id = lo_flow.gauge_id AND lo_flow.data_type = 'flow'
LEFT JOIN latest_gauge_observation lo_gage
       ON g.id = lo_gage.gauge_id AND lo_gage.data_type = 'gauge'
LEFT JOIN (
    SELECT reach_id, MIN(low) AS low, MAX(high) AS high
    FROM reach_class
    WHERE low_data_type = 'flow' AND low IS NOT NULL AND high IS NOT NULL
    GROUP BY reach_id
) rc_range ON rc_range.reach_id = r.id
WHERE r.no_show = 0 AND st.name IN ($placeholders)
ORDER BY r.sort_name
SQL;

    $stmt = $db->prepare($sql);
    $stmt->execute($state_names);
    $rows = $stmt->fetchAll();

    // Tiers come from GROUP_CONCAT over reach_class; parse + join with "," for
    // the client (which splits the data-tier attribute on comma).
    $reach_ids = array_column($rows, 'id');
    $tiers_by_reach = [];
    if ($reach_ids !== []) {
        $ph = implode(',', array_fill(0, count($reach_ids), '?'));
        $cls_stmt = $db->prepare(
            "SELECT reach_id, name FROM reach_class WHERE reach_id IN ($ph)"
        );
        $cls_stmt->execute($reach_ids);
        $raw_by_reach = [];
        foreach ($cls_stmt->fetchAll() as $cr) {
            $raw_by_reach[(int)$cr['reach_id']][] = $cr['name'];
        }
        foreach ($raw_by_reach as $rid => $names) {
            $merged = [];
            foreach ($names as $n) {
                foreach (parse_class_tiers($n) as $t) $merged[$t] = true;
            }
            $order = array_flip(['I', 'II', 'III', 'IV', 'V']);
            uksort($merged, fn($a, $b) => $order[$a] <=> $order[$b]);
            $tiers_by_reach[$rid] = array_keys($merged);
        }
    }

    foreach ($rows as &$row) {
        $rid = (int)$row['id'];
        $row['h'] = pubhash_encode($rid);
        $row['tiers'] = $tiers_by_reach[$rid] ?? [];
        $row['status'] = $row['status'] ?? 'unknown';
    }
    unset($row);

    echo json_encode($rows, JSON_UNESCAPED_UNICODE);
    exit;
}

// -----------------------------------------------------------------------
// HTML page
// -----------------------------------------------------------------------
// Canonicalize a legacy ?ids=<decimal,…> bookmark to ?h=<handle,…> before any
// output; picker.js then reads only ?h=.
pubhash_redirect_legacy_ids();

require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

// Server-render pill lists so filters.js can wire them up without an
// additional round trip. Also keep the auto-checked primary state so the
// picker table populates immediately on first load.
$all_states = array_column(
    db_query($db, 'SELECT DISTINCT st.name FROM state st
                JOIN reach_state rs ON st.id = rs.state_id
                JOIN reach r ON rs.reach_id = r.id
                WHERE r.no_show = 0 ORDER BY st.name')->fetchAll(),
    'name'
);

// Optional ?state=<full name> — when present and valid, that state becomes
// the auto-checked primary so users arriving from a state landing page
// (e.g. Oregon.html) land in a picker focused on their current state.
// Falls back to Oregon when missing, empty, or not in $all_states.
$state_param = filter_input(INPUT_GET, 'state', FILTER_DEFAULT);
$primary_state = (is_string($state_param) && in_array($state_param, $all_states, true))
    ? $state_param
    : 'Oregon';
$all_basins = array_column(
    db_query($db, "SELECT DISTINCT basin FROM reach
                WHERE no_show = 0 AND basin IS NOT NULL AND basin != ''
                ORDER BY basin")->fetchAll(),
    'basin'
);

$status_meta = [
    'low'     => ['label' => 'Low',     'swatch' => '#e8a735'],
    'okay'    => ['label' => 'Okay',    'swatch' => '#4caf50'],
    'high'    => ['label' => 'High',    'swatch' => '#e53935'],
    'unknown' => ['label' => 'Unknown', 'swatch' => '#2196F3'],
];
$class_tiers = ['I', 'II', 'III', 'IV', 'V', '?'];

include_header('Build Your Own Levels Page', 'picker');
?>
<h2>Build Your Own Levels Page</h2>
<p style="margin:.5rem 0;font-size:.85rem">Pick reaches, then view a custom levels page you can bookmark and share.</p>

<div class="picker-states" id="state-pills" data-auto="<?= htmlspecialchars($primary_state) ?>">
<?php foreach ($all_states as $name): $checked = $name === $primary_state ? ' checked' : ''; ?>
  <label><input type="checkbox" value="<?= htmlspecialchars($name) ?>"<?= $checked ?>><span><?= htmlspecialchars($name) ?></span></label>
<?php endforeach; ?>
</div>

<?php
$fg_toggle = '<span class="fg-toggle">'
           . '<button type="button" data-all>All</button>'
           . '<button type="button" data-none>None</button>'
           . '</span>';
?>
<div class="filter-bar" id="filter-bar" hidden>
  <details class="filter-group">
    <summary>Basin <span class="fg-count"><?= count($all_basins) ?></span></summary>
    <div class="filter-pills" data-group="basin">
      <?= $fg_toggle ?>
<?php foreach ($all_basins as $b): ?>
      <label><input type="checkbox" value="<?= htmlspecialchars($b) ?>" checked><?= htmlspecialchars($b) ?></label>
<?php endforeach; ?>
    </div>
  </details>
  <details class="filter-group">
    <summary>Status <span class="fg-count">4</span></summary>
    <div class="filter-pills" data-group="status">
      <?= $fg_toggle ?>
<?php foreach ($status_meta as $key => $m): ?>
      <label><input type="checkbox" value="<?= htmlspecialchars($key) ?>" checked><span class="swatch" style="background:<?= $m['swatch'] ?>"></span><?= htmlspecialchars($m['label']) ?></label>
<?php endforeach; ?>
    </div>
  </details>
  <details class="filter-group">
    <summary>Class <span class="fg-count"><?= count($class_tiers) ?></span></summary>
    <div class="filter-pills" data-group="tier" data-split="csv">
      <?= $fg_toggle ?>
<?php foreach ($class_tiers as $t): ?>
      <label><input type="checkbox" value="<?= htmlspecialchars($t) ?>" checked><?= htmlspecialchars($t) ?></label>
<?php endforeach; ?>
    </div>
  </details>
  <div class="filter-meta" aria-live="polite">
    <span class="fb-count"></span>
    <button type="button" class="fb-reset">Reset</button>
  </div>
</div>

<label for="search" class="sr-only">Filter reaches by name</label>
<input type="text" class="picker-search" id="search" placeholder="Filter reaches by name…">

<table class="picker-sections" id="sections">
  <thead>
    <tr>
      <th style="width:1%"><label><input type="checkbox" id="select-all"><span class="sr-only"> Select all</span></label></th>
      <th>Name</th>
      <th class="col-location">Location</th>
      <th class="col-flow">Flow</th>
      <th class="col-gage">Gage</th>
      <th>Status</th>
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
$filters_mtime_raw = @filemtime($_SERVER['DOCUMENT_ROOT'] . '/static/filters.js');
$filters_mtime = $filters_mtime_raw !== false ? $filters_mtime_raw : 1;
$picker_mtime_raw  = @filemtime($_SERVER['DOCUMENT_ROOT'] . '/static/picker.js');
$picker_mtime  = $picker_mtime_raw !== false ? $picker_mtime_raw : 1;
?>
<script src="/static/filters.js?v=<?= $filters_mtime ?>" defer></script>
<script src="/static/picker.js?v=<?= $picker_mtime ?>" defer></script>
<?php
include_footer();
