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
LEFT JOIN (
    SELECT gauge_id, MIN(source_id) AS source_id
    FROM gauge_source GROUP BY gauge_id
) gs ON g.id = gs.gauge_id
LEFT JOIN latest_observation lo_flow
       ON gs.source_id = lo_flow.source_id AND lo_flow.data_type = 'flow'
LEFT JOIN latest_observation lo_gage
       ON gs.source_id = lo_gage.source_id AND lo_gage.data_type = 'gauge'
WHERE r.no_show = 0 AND st.name IN ($placeholders)
ORDER BY r.sort_name
SQL;

    $stmt = $db->prepare($sql);
    $stmt->execute($state_names);
    $rows = $stmt->fetchAll();

    echo json_encode($rows, JSON_UNESCAPED_UNICODE);
    exit;
}

// -----------------------------------------------------------------------
// Load all states for the filter pills
// -----------------------------------------------------------------------
$states = $db->query(
    'SELECT DISTINCT st.name FROM state st
     JOIN reach_state rs ON st.id = rs.state_id
     JOIN reach r ON rs.reach_id = r.id
     WHERE r.no_show = 0
     ORDER BY st.name'
)->fetchAll(PDO::FETCH_COLUMN);

// -----------------------------------------------------------------------
// HTML page
// -----------------------------------------------------------------------
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

include_header('Build Your Own Levels Page', 'picker');
?>
<h2>Build Your Own Levels Page</h2>
<p style="margin:.5rem 0;font-size:.85rem">Select states, pick reaches, then view a custom levels page you can bookmark and share.</p>

<div class="picker-states" id="state-pills">
<?php foreach ($states as $st): ?>
  <label><input type="checkbox" value="<?= htmlspecialchars($st) ?>"><span><?= htmlspecialchars($st) ?></span></label>
<?php endforeach; ?>
</div>

<input type="text" class="picker-search" id="search" placeholder="Filter reaches by name…" disabled>

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

<script>
(function() {
  const stateCache = new Map();
  const selected = new Set();
  const pills = document.getElementById('state-pills');
  const tbody = document.getElementById('tbody');
  const search = document.getElementById('search');
  const actions = document.getElementById('actions');
  const countEl = document.getElementById('count');
  const viewLink = document.getElementById('view-link');
  const copyBtn = document.getElementById('copy-btn');
  const copiedEl = document.getElementById('copied');
  const selectAll = document.getElementById('select-all');
  let allRows = [];

  function checkedStates() {
    return Array.from(pills.querySelectorAll('input:checked')).map(cb => cb.value);
  }

  function statusHtml(delta) {
    if (delta === null || delta === undefined) return '';
    const d = parseFloat(delta);
    if (Math.abs(d) < 0.5) return '<span class="stable">stable</span>';
    return d > 0 ? '<span class="rising">rising</span>' : '<span class="falling">falling</span>';
  }

  function fmtFlow(v) { return v !== null && v !== undefined ? Math.round(v).toLocaleString() : ''; }
  function fmtGage(v) { return v !== null && v !== undefined ? parseFloat(v).toFixed(2) : ''; }

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  function buildAllRows() {
    const byId = new Map();
    for (const name of checkedStates()) {
      const rows = stateCache.get(name);
      if (!rows) continue;
      for (const r of rows) byId.set(r.id, r);
    }
    allRows = Array.from(byId.values()).sort(
      (a, b) => (a.sort_name || '').localeCompare(b.sort_name || '')
    );
  }

  function renderTable() {
    const q = search.value.toLowerCase();
    const html = [];
    for (const r of allRows) {
      const name = r.name || '';
      if (q && !name.toLowerCase().includes(q)) continue;
      const chk = selected.has(r.id) ? ' checked' : '';
      html.push(
        '<tr>',
        '<td><input type="checkbox" data-id="' + r.id + '"' + chk + '></td>',
        '<td>' + esc(name) + '</td>',
        '<td class="col-location">' + esc(r.location || '') + '</td>',
        '<td class="col-flow">' + fmtFlow(r.flow) + '</td>',
        '<td class="col-gage">' + fmtGage(r.gage) + '</td>',
        '<td>' + statusHtml(r.flow_delta) + '</td>',
        '</tr>'
      );
    }
    tbody.innerHTML = html.join('');
    updateActions();
  }

  function updateActions() {
    const n = selected.size;
    countEl.textContent = n + ' selected';
    actions.style.display = n || allRows.length ? '' : 'none';
    if (n) {
      const ids = Array.from(selected).join(',');
      const url = location.origin + '/custom.php?ids=' + ids;
      viewLink.href = url;
      viewLink.classList.remove('disabled');
    } else {
      viewLink.href = '#';
      viewLink.classList.add('disabled');
    }
  }

  async function loadStates() {
    const names = checkedStates();
    if (!names.length) {
      allRows = [];
      search.disabled = true;
      renderTable();
      return;
    }
    const needed = names.filter(n => !stateCache.has(n));
    if (needed.length) {
      const url = '/picker.php?ajax=1&states=' + encodeURIComponent(needed.join(','));
      const resp = await fetch(url);
      const rows = await resp.json();
      for (const n of needed) stateCache.set(n, rows);
    }
    buildAllRows();
    search.disabled = false;
    renderTable();
  }

  pills.addEventListener('change', loadStates);
  search.addEventListener('input', renderTable);

  tbody.addEventListener('change', function(e) {
    if (e.target.type !== 'checkbox') return;
    const id = parseInt(e.target.dataset.id);
    if (e.target.checked) selected.add(id);
    else selected.delete(id);
    updateActions();
  });

  selectAll.addEventListener('change', function() {
    const checked = selectAll.checked;
    const q = search.value.toLowerCase();
    for (const r of allRows) {
      if (q && !(r.name || '').toLowerCase().includes(q)) continue;
      if (checked) selected.add(r.id);
      else selected.delete(r.id);
    }
    renderTable();
  });

  copyBtn.addEventListener('click', function() {
    const url = viewLink.href;
    if (!url || url === '#') return;
    navigator.clipboard.writeText(url).then(function() {
      copiedEl.style.display = '';
      setTimeout(function() { copiedEl.style.display = 'none'; }, 2000);
    });
  });
})();
</script>
<?php
include_footer();
