<?php
/**
 * Picker — "Build Your Own Levels Page"
 *
 * HTML page: state filter pills, text search, reach checklist.
 * AJAX endpoint (?ajax=1&states=Washington,Oregon): returns JSON reaches.
 */
require_once __DIR__ . '/includes/db.php';

$db = get_db();

// -----------------------------------------------------------------------
// AJAX JSON endpoint
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
       g.location,
       lo_flow.value       AS flow,
       lo_gage.value       AS gage,
       lo_flow.delta_per_hour AS flow_delta
FROM reach r
JOIN reach_state rs ON r.id = rs.reach_id
JOIN state st ON rs.state_id = st.id
LEFT JOIN gauge g ON r.gauge_id = g.id
LEFT JOIN latest_gauge_observation lo_flow
       ON g.id = lo_flow.gauge_id AND lo_flow.data_type = 'flow'
LEFT JOIN latest_gauge_observation lo_gage
       ON g.id = lo_gage.gauge_id AND lo_gage.data_type = 'gauge'
WHERE r.no_show = 0 AND st.name IN ($placeholders)
ORDER BY r.sort_name
SQL;

    $stmt = $db->prepare($sql);
    $stmt->execute($state_names);
    $rows = $stmt->fetchAll();

    echo json_encode($rows, JSON_UNESCAPED_UNICODE);
    exit;
}

// Primary state — picker only shows reaches from the index page
$primary_state = 'Oregon';

// -----------------------------------------------------------------------
// HTML page
// -----------------------------------------------------------------------
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

include_header('Build Your Own Levels Page', 'picker');
?>
<h2>Build Your Own Levels Page</h2>
<p style="margin:.5rem 0;font-size:.85rem">Pick reaches, then view a custom levels page you can bookmark and share.</p>

<div class="picker-states" id="state-pills" data-auto="<?= htmlspecialchars($primary_state) ?>" style="display:none">
  <label><input type="checkbox" value="<?= htmlspecialchars($primary_state) ?>" checked><span><?= htmlspecialchars($primary_state) ?></span></label>
</div>

<input type="text" class="picker-search" id="search" placeholder="Filter reaches by name…">

<table class="picker-sections" id="sections">
  <thead>
    <tr>
      <th style="width:1%"><input type="checkbox" id="select-all" title="Select all / deselect all"></th>
      <th>Name</th>
      <th class="col-location">Location</th>
      <th class="col-flow">Flow</th>
      <th class="col-gage">Gage</th>
      <th>Status</th>
    </tr>
  </thead>
  <tbody id="tbody"></tbody>
</table>

<div class="picker-actions" id="actions" style="display:none">
  <span class="count" id="count">0 selected</span>
  <a id="view-link" class="disabled" href="#">View Custom Page</a>
  <button id="copy-btn" type="button">Copy Link</button>
  <span class="copied" id="copied" style="display:none">Copied!</span>
</div>

<script src="/static/picker.js?v=2"></script>
<?php
include_footer();
