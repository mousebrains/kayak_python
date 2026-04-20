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
  let filterBar = null;

  window.kayakFilters = window.kayakFilters || {};
  window.kayakFilters._manualInit = true;  // we'll call init() after first fetch

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
    d.textContent = s == null ? '' : s;
    return d.innerHTML;
  }

  function buildAllRows() {
    const byId = new Map();
    for (const name of checkedStates()) {
      const rows = stateCache.get(name);
      if (!rows) continue;
      for (const r of rows) byId.set(r.id, r);
    }
    // Fall back through sort_name → name so rows with NULL sort_name land
    // in their alphabetical home instead of sorting to the top.
    allRows = Array.from(byId.values()).sort((a, b) => {
      const keyA = (a.sort_name || a.name || '').toString();
      const keyB = (b.sort_name || b.name || '').toString();
      return keyA.localeCompare(keyB);
    });
  }

  function renderTable() {
    const q = search.value.toLowerCase();
    const html = [];
    for (const r of allRows) {
      const name = r.name || '';
      if (q && !name.toLowerCase().includes(q)) continue;
      const chk = selected.has(r.id) ? ' checked' : '';
      const tiers = (r.tiers && r.tiers.length) ? r.tiers.join(',') : '?';
      html.push(
        '<tr data-state="' + esc(r.state_first || '') + '"' +
        ' data-basin="' + esc(r.basin || '') + '"' +
        ' data-status="' + esc(r.status || 'unknown') + '"' +
        ' data-tier="' + esc(tiers) + '">',
        '<td><label><input type="checkbox" data-id="' + r.id + '"' + chk + '><span class="sr-only"> Select ' + esc(name) + '</span></label></td>',
        '<td>' + esc(name) + '</td>',
        '<td class="col-location">' + esc(r.location || '') + '</td>',
        '<td class="col-flow">' + fmtFlow(r.flow) + '</td>',
        '<td class="col-gage">' + fmtGage(r.gage) + '</td>',
        '<td>' + statusHtml(r.flow_delta) + '</td>',
        '</tr>'
      );
    }
    tbody.innerHTML = html.join('');
    reinitFilters();
    updateActions();
  }

  function reinitFilters() {
    if (!window.kayakFilters || !window.kayakFilters.init) return;
    filterBar = window.kayakFilters.init({
      barContainer: document.getElementById('filter-bar'),
      rowsContainer: tbody,
      onChange: function() { /* count/select-all accounting below */ updateActions(); },
    });
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
      try {
        const url = '/picker.php?ajax=1&states=' + encodeURIComponent(needed.join(','));
        const resp = await fetch(url);
        const rows = await resp.json();
        // Stamp the state name on each row so data-state is populated for
        // the shared filter module. Rows belonging to multiple checked
        // states show up once per state in the response; picker dedupes
        // by id so we pick the first state name we see.
        for (const n of needed) {
          const tagged = rows.map(r => Object.assign({}, r, {state_first: r.state_first || n}));
          stateCache.set(n, tagged);
        }
      } catch {
        return;  // keep stale data on network error
      }
    }
    buildAllRows();
    search.disabled = false;
    renderTable();
  }

  pills.addEventListener('change', loadStates);
  search.addEventListener('input', renderTable);

  if (checkedStates().length) loadStates();

  tbody.addEventListener('change', function(e) {
    if (e.target.type !== 'checkbox') return;
    const id = parseInt(e.target.dataset.id, 10);
    if (e.target.checked) selected.add(id);
    else selected.delete(id);
    updateActions();
  });

  selectAll.addEventListener('change', function() {
    const checked = selectAll.checked;
    const q = search.value.toLowerCase();
    // Only toggle rows currently visible (both text-matched and filter-matched).
    tbody.querySelectorAll('tr:not([hidden])').forEach(function(tr) {
      const cb = tr.querySelector('input[data-id]');
      if (!cb) return;
      const id = parseInt(cb.dataset.id, 10);
      const label = tr.querySelector('td:nth-child(2)').textContent.toLowerCase();
      if (q && !label.includes(q)) return;
      cb.checked = checked;
      if (checked) selected.add(id); else selected.delete(id);
    });
    updateActions();
  });

  copyBtn.addEventListener('click', function() {
    const url = viewLink.href;
    if (!url || url === '#') return;
    navigator.clipboard.writeText(url).then(function() {
      copiedEl.style.display = '';
      setTimeout(function() { copiedEl.style.display = 'none'; }, 2000);
    }).catch(function() {});
  });
})();
