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
      try {
        const url = '/picker.php?ajax=1&states=' + encodeURIComponent(needed.join(','));
        const resp = await fetch(url);
        const rows = await resp.json();
        for (const n of needed) stateCache.set(n, rows);
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

  // Auto-load if a state is pre-checked
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
    }).catch(function() {});
  });
})();
