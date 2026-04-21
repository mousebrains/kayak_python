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

$db = get_db();

// -----------------------------------------------------------------------
// AJAX JSON endpoint — returns one row per reach with enough metadata
// for client-side filtering (state/basin/status/tiers) and display.
// -----------------------------------------------------------------------
if (filter_input(INPUT_GET, 'ajax', FILTER_VALIDATE_INT)) {
    header('Content-Type: application/json');
    header('Cache-Control: max-age=60');

    $raw = filter_input(INPUT_GET, 'states', FILTER_DEFAULT) ?? '';
    $state_names = array_filter(array_map('trim', explode(',', $raw)));
    if (!$state_names) {
        echo '[]';
        exit;
    }

    $placeholders = implode(',', array_fill(0, count($state_names), '?'));

    $sql = <<<SQL
SELECT DISTINCT r.id,
       COALESCE(r.display_name, r.name) AS name,
       r.sort_name,
       r.basin                        AS basin,
       g.location,
       lo_flow.value                  AS flow,
       lo_gage.value                  AS gage,
       lo_flow.delta_per_hour         AS flow_delta,
       rl.level                       AS status
FROM reach r
JOIN reach_state rs ON r.id = rs.reach_id
JOIN state st ON rs.state_id = st.id
LEFT JOIN gauge g ON r.gauge_id = g.id
LEFT JOIN latest_gauge_observation lo_flow
       ON g.id = lo_flow.gauge_id AND lo_flow.data_type = 'flow'
LEFT JOIN latest_gauge_observation lo_gage
       ON g.id = lo_gage.gauge_id AND lo_gage.data_type = 'gauge'
LEFT JOIN reach_level rl
       ON rl.reach_id = r.id
      AND rl.low_data_type = 'flow'
      AND rl.low  <= lo_flow.value
      AND lo_flow.value <= rl.high
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
    if ($reach_ids) {
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
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

// Server-render pill lists so filters.js can wire them up without an
// additional round trip. Also keep the auto-checked primary state so the
// picker table populates immediately on first load.
$primary_state = 'Oregon';

$all_states = array_column(
    $db->query('SELECT DISTINCT st.name FROM state st
                JOIN reach_state rs ON st.id = rs.state_id
                JOIN reach r ON rs.reach_id = r.id
                WHERE r.no_show = 0 ORDER BY st.name')->fetchAll(),
    'name'
);
$all_basins = array_column(
    $db->query("SELECT DISTINCT basin FROM reach
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
  <ol id="selected-list" class="selected-list" aria-live="polite"></ol>
</section>

<div class="picker-actions" id="actions" style="display:none">
  <span class="count" id="count">0 selected</span>
  <a id="view-link" class="disabled" href="#">View Custom Page</a>
  <button id="copy-btn" type="button">Copy Link</button>
  <span class="copied" id="copied" style="display:none">Copied!</span>
</div>

<?php
$filters_mtime = @filemtime($_SERVER['DOCUMENT_ROOT'] . '/static/filters.js') ?: 1;
$picker_mtime  = @filemtime($_SERVER['DOCUMENT_ROOT'] . '/static/picker.js')  ?: 1;
?>
<script src="/static/filters.js?v=<?= $filters_mtime ?>" defer></script>
<script src="/static/picker.js?v=<?= $picker_mtime ?>" defer></script>
<?php
include_footer();
