(function() {
  const stateCache = new Map();         // state name -> [row, ...]
  const byId       = new Map();         // reach id  -> row (persists across state toggles)
  let selectedList = [];                // reach ids in display order
  const selectedSet = new Set();        // mirror of selectedList for O(1) .has()

  const pills    = document.getElementById('state-pills');
  const tbody    = document.getElementById('tbody');
  const search   = document.getElementById('search');
  const actions  = document.getElementById('actions');
  const countEl  = document.getElementById('count');
  const viewLink = document.getElementById('view-link');
  const copyBtn  = document.getElementById('copy-btn');
  const copiedEl = document.getElementById('copied');
  const selectAll = document.getElementById('select-all');
  const ol       = document.getElementById('selected-list');
  const clearBtn = document.getElementById('clear-btn');

  let allRows = [];

  window.kayakFilters = window.kayakFilters || {};
  window.kayakFilters._manualInit = true;  // we'll call init() after first fetch

  // -----------------------------------------------------------------
  // Selection state primitives
  // -----------------------------------------------------------------
  function add(id) {
    if (selectedSet.has(id)) return;
    selectedList.push(id);
    selectedSet.add(id);
  }
  function remove(id) {
    if (!selectedSet.has(id)) return;
    selectedSet.delete(id);
    selectedList = selectedList.filter(x => x !== id);
  }
  function move(from, to) {
    const [v] = selectedList.splice(from, 1);
    selectedList.splice(to, 0, v);
  }
  function syncCheckbox(id, checked) {
    const cb = tbody.querySelector('input[data-id="' + id + '"]');
    if (cb) cb.checked = checked;
  }

  // -----------------------------------------------------------------
  // Helpers
  // -----------------------------------------------------------------
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

  // -----------------------------------------------------------------
  // Available-table rendering
  // -----------------------------------------------------------------
  function buildAllRows() {
    const byIdLocal = new Map();
    for (const name of checkedStates()) {
      const rows = stateCache.get(name);
      if (!rows) continue;
      for (const r of rows) byIdLocal.set(r.id, r);
    }
    allRows = Array.from(byIdLocal.values()).sort((a, b) => {
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
      const chk = selectedSet.has(r.id) ? ' checked' : '';
      const tiers = (r.tiers?.length) ? r.tiers.join(',') : '?';
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
    if (!window.kayakFilters?.init) return;
    window.kayakFilters.init({
      barContainer: document.getElementById('filter-bar'),
      rowsContainer: tbody,
      onChange: function() { updateActions(); },
    });
  }

  // -----------------------------------------------------------------
  // Selected-list rendering
  // -----------------------------------------------------------------
  function renderSelected() {
    if (!selectedList.length) {
      ol.innerHTML = '<li class="empty">No reaches selected yet — tap a checkbox above to add.</li>';
      return;
    }
    const total = selectedList.length;
    ol.innerHTML = selectedList.map(function(id, i) {
      const r = byId.get(id);
      const name = (r && (r.name || r.display_name)) || ('reach #' + id);
      const safeName = esc(name);
      const ariaLabel = safeName + ', position ' + (i + 1) + ' of ' + total;
      return '<li data-id="' + id + '" tabindex="0" aria-label="' + ariaLabel + '">' +
             '<button type="button" class="drag-handle" aria-label="Drag to reorder, or use Up/Down arrows to move and Delete to remove">☰</button>' +
             '<span class="num">' + (i + 1) + '.</span>' +
             '<span class="name">' + safeName + '</span>' +
             '<button type="button" class="remove-btn" aria-label="Remove ' + safeName + '">✕</button>' +
             '</li>';
    }).join('');
  }

  // -----------------------------------------------------------------
  // Action area (count, View link, Copy)
  // -----------------------------------------------------------------
  function updateActions() {
    const n = selectedList.length;
    countEl.textContent = n + ' selected';
    actions.style.display = (n || allRows.length) ? '' : 'none';
    if (n) {
      const url = location.origin + '/custom.php?ids=' + selectedList.join(',');
      viewLink.href = url;
      viewLink.classList.remove('disabled');
    } else {
      viewLink.href = '#';
      viewLink.classList.add('disabled');
    }
  }

  // -----------------------------------------------------------------
  // State pill change → fetch reaches
  // -----------------------------------------------------------------
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
        for (const n of needed) {
          const tagged = rows.map(r => Object.assign({}, r, {state_first: r.state_first || n}));
          stateCache.set(n, tagged);
          // Persist by id so the bottom list can label even after a state
          // pill is unchecked.
          for (const r of tagged) byId.set(r.id, r);
        }
      } catch {
        return;  // keep stale data on network error
      }
    }
    buildAllRows();
    search.disabled = false;
    renderTable();
  }

  // -----------------------------------------------------------------
  // Drag & drop on the selected list (pointer events: mouse + touch + pen)
  // -----------------------------------------------------------------
  let dragEl = null, placeholder = null;

  ol.addEventListener('pointerdown', function(e) {
    if (dragEl) return;  // ignore second simultaneous touch
    const handle = e.target.closest('.drag-handle');
    if (!handle) return;
    e.preventDefault();
    dragEl = handle.closest('li');
    handle.setPointerCapture(e.pointerId);
    dragEl.classList.add('dragging');
    placeholder = document.createElement('li');
    placeholder.className = 'drop-target';
    placeholder.style.height = dragEl.offsetHeight + 'px';
    ol.insertBefore(placeholder, dragEl.nextSibling);
  });

  ol.addEventListener('pointermove', function(e) {
    if (!dragEl) return;
    const items = ol.querySelectorAll('li:not(.dragging):not(.drop-target)');
    let placed = false;
    for (const li of items) {
      const rect = li.getBoundingClientRect();
      if (e.clientY < rect.top + rect.height / 2) {
        ol.insertBefore(placeholder, li);
        placed = true;
        break;
      }
    }
    if (!placed) ol.appendChild(placeholder);
  });

  function endDrag(commit) {
    if (!dragEl) return;
    if (commit && placeholder) ol.insertBefore(dragEl, placeholder);
    if (placeholder) placeholder.remove();
    dragEl.classList.remove('dragging');
    dragEl = null;
    placeholder = null;
    if (commit) {
      selectedList = Array.from(ol.querySelectorAll('li[data-id]'))
        .map(li => parseInt(li.dataset.id, 10));
      selectedSet.clear();
      for (const id of selectedList) selectedSet.add(id);
      renderSelected();
      updateActions();
    }
  }
  ol.addEventListener('pointerup',     function() { endDrag(true); });
  ol.addEventListener('pointercancel', function() { endDrag(false); });

  // -----------------------------------------------------------------
  // Keyboard reorder + remove
  // -----------------------------------------------------------------
  ol.addEventListener('keydown', function(e) {
    const li = e.target.closest('li[data-id]');
    if (!li) return;
    const id = parseInt(li.dataset.id, 10);
    const idx = selectedList.indexOf(id);
    if (idx < 0) return;
    let action = null;
    if (e.key === 'ArrowUp' && idx > 0) {
      move(idx, idx - 1); action = 'move';
    } else if (e.key === 'ArrowDown' && idx < selectedList.length - 1) {
      move(idx, idx + 1); action = 'move';
    } else if (e.key === 'Delete' || e.key === 'Backspace') {
      remove(id); syncCheckbox(id, false); action = 'remove';
    } else {
      return;
    }
    e.preventDefault();
    renderSelected();
    if (action === 'move') {
      const target = ol.querySelector('li[data-id="' + id + '"]');
      if (target) target.focus();
    }
    updateActions();
  });

  // -----------------------------------------------------------------
  // ✕ remove button click
  // -----------------------------------------------------------------
  ol.addEventListener('click', function(e) {
    const btn = e.target.closest('.remove-btn');
    if (!btn) return;
    const li = btn.closest('li[data-id]');
    if (!li) return;
    const id = parseInt(li.dataset.id, 10);
    remove(id);
    syncCheckbox(id, false);
    renderSelected();
    updateActions();
  });

  // -----------------------------------------------------------------
  // Available-table checkbox change
  // -----------------------------------------------------------------
  tbody.addEventListener('change', function(e) {
    if (e.target.type !== 'checkbox') return;
    const id = parseInt(e.target.dataset.id, 10);
    if (e.target.checked) add(id); else remove(id);
    renderSelected();
    updateActions();
  });

  // -----------------------------------------------------------------
  // Select-all (visible rows) + Clear (whole selection)
  // -----------------------------------------------------------------
  selectAll.addEventListener('change', function() {
    const checked = selectAll.checked;
    const q = search.value.toLowerCase();
    tbody.querySelectorAll('tr:not([hidden])').forEach(function(tr) {
      const cb = tr.querySelector('input[data-id]');
      if (!cb) return;
      const id = parseInt(cb.dataset.id, 10);
      const label = tr.querySelector('td:nth-child(2)').textContent.toLowerCase();
      if (q && !label.includes(q)) return;
      cb.checked = checked;
      if (checked) add(id); else remove(id);
    });
    renderSelected();
    updateActions();
  });

  if (clearBtn) {
    clearBtn.addEventListener('click', function() {
      selectedList = [];
      selectedSet.clear();
      tbody.querySelectorAll('input[data-id]').forEach(cb => { cb.checked = false; });
      renderSelected();
      updateActions();
    });
  }

  // -----------------------------------------------------------------
  // Copy link
  // -----------------------------------------------------------------
  copyBtn.addEventListener('click', function() {
    const url = viewLink.href;
    if (!url || url === '#') return;
    navigator.clipboard.writeText(url).then(function() {
      copiedEl.style.display = '';
      setTimeout(function() { copiedEl.style.display = 'none'; }, 2000);
    }).catch(function() {});
  });

  // -----------------------------------------------------------------
  // State pill change + search input
  // -----------------------------------------------------------------
  pills.addEventListener('change', loadStates);
  search.addEventListener('input', renderTable);

  // -----------------------------------------------------------------
  // URL pre-population: ?ids=5,12,7 on picker.php load
  // -----------------------------------------------------------------
  function readIdsFromUrl() {
    const m = location.search.match(/[?&]ids=([^&]+)/);
    if (!m) return [];
    return m[1].split(',').map(s => parseInt(s, 10)).filter(n => n > 0);
  }
  const initialIds = readIdsFromUrl();
  if (initialIds.length) {
    initialIds.forEach(add);
    // Auto-check every state pill so the available table can find the
    // reaches and tick their checkboxes (and byId picks up names).
    pills.querySelectorAll('input[type=checkbox]').forEach(cb => { cb.checked = true; });
  }
  renderSelected();
  if (checkedStates().length) {
    loadStates().then(renderSelected);
  }
})();
