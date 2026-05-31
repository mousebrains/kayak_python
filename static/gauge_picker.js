/* gauge_picker.js — sibling of picker.js for gauges.
 *
 * Differences from picker.js:
 *   - Endpoint /gauge_picker.php?ajax=1&states=...
 *   - Row data attributes are state + huc8 (no basin/status/tier).
 *   - Display label is "<river> at <location>" (or just river if no location).
 *   - Result URL is /custom_gauges.php?h=...
 */
(function () {
  'use strict';
  const stateCache = new Map(); // state name -> [row, ...]
  // Selection identity is the public base-62 handle (an opaque string), read
  // from each row's `h` field / the checkbox data-h attribute — never the
  // numeric id, so the ?h= URL round-trips without a client-side codec.
  const byHandle = new Map(); // gauge handle -> row (persists across state toggles)
  let selectedList = []; // gauge handles in display order
  const selectedSet = new Set(); // mirror for O(1) .has()

  const pills = document.getElementById('state-pills');
  const tbody = document.getElementById('tbody');
  const search = document.getElementById('search');
  const actions = document.getElementById('actions');
  const countEl = document.getElementById('count');
  const viewLink = document.getElementById('view-link');
  const copyBtn = document.getElementById('copy-btn');
  const copiedEl = document.getElementById('copied');
  const selectAll = document.getElementById('select-all');
  const ol = document.getElementById('selected-list');
  const clearBtn = document.getElementById('clear-btn');
  const editOrderBtn = document.getElementById('edit-order-btn');

  let allRows = [];

  window.kayakFilters = window.kayakFilters || {};
  window.kayakFilters._manualInit = true; // we'll call init() after first fetch

  function add(id) {
    if (selectedSet.has(id)) return;
    selectedList.push(id);
    selectedSet.add(id);
  }
  function remove(id) {
    if (!selectedSet.has(id)) return;
    selectedSet.delete(id);
    selectedList = selectedList.filter((x) => x !== id);
  }
  function move(from, to) {
    const [v] = selectedList.splice(from, 1);
    selectedList.splice(to, 0, v);
  }
  function syncCheckbox(id, checked) {
    const cb = tbody.querySelector('input[data-h="' + id + '"]');
    if (cb) cb.checked = checked;
  }

  function checkedStates() {
    return Array.from(pills.querySelectorAll('input:checked')).map(
      (cb) => cb.value,
    );
  }
  function fmtFlow(v) {
    return v !== null && v !== undefined ? Math.round(v).toLocaleString() : '';
  }
  function fmtGage(v) {
    return v !== null && v !== undefined ? parseFloat(v).toFixed(2) : '';
  }
  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : s;
    return d.innerHTML;
  }
  function gaugeLabel(r) {
    const river = r.river || r.name || '';
    const loc = r.location || '';
    return loc ? river + ' at ' + loc : river;
  }

  function buildAllRows() {
    const byHandleLocal = new Map();
    for (const name of checkedStates()) {
      const rows = stateCache.get(name);
      if (!rows) continue;
      for (const r of rows) byHandleLocal.set(r.h, r);
    }
    allRows = Array.from(byHandleLocal.values()).sort((a, b) => {
      const keyA = (a.sort_name || a.river || '').toString();
      const keyB = (b.sort_name || b.river || '').toString();
      return keyA.localeCompare(keyB);
    });
  }

  function renderTable() {
    const q = search.value.toLowerCase();
    const html = [];
    for (const r of allRows) {
      const label = gaugeLabel(r);
      if (q && !label.toLowerCase().includes(q)) continue;
      const chk = selectedSet.has(r.h) ? ' checked' : '';
      html.push(
        '<tr data-state="' +
          esc(r.state || '') +
          '"' +
          ' data-huc8="' +
          esc(r.huc8 || '') +
          '">',
        '<td><label><input type="checkbox" data-h="' +
          r.h +
          '"' +
          chk +
          '><span class="sr-only"> Select ' +
          esc(label) +
          '</span></label></td>',
        '<td>' + esc(r.river || '') + '</td>',
        '<td class="col-location">' + esc(r.location || '') + '</td>',
        '<td class="col-flow">' + fmtFlow(r.flow) + '</td>',
        '<td class="col-gage">' + fmtGage(r.gage) + '</td>',
        '</tr>',
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
      onChange: function () {
        updateActions();
      },
    });
  }

  function renderSelected() {
    if (!selectedList.length) {
      ol.innerHTML =
        '<li class="empty">No gauges selected yet — tap a checkbox above to add.</li>';
      return;
    }
    const total = selectedList.length;
    ol.innerHTML = selectedList
      .map(function (id, i) {
        const r = byHandle.get(id);
        const label = (r && gaugeLabel(r)) || 'gauge #' + id;
        const safeName = esc(label);
        const ariaLabel = safeName + ', position ' + (i + 1) + ' of ' + total;
        return (
          '<li data-h="' +
          id +
          '" tabindex="0" aria-label="' +
          ariaLabel +
          '">' +
          '<button type="button" class="drag-handle" aria-label="Drag to reorder, or use Up/Down arrows to move and Delete to remove">☰</button>' +
          '<span class="num">' +
          (i + 1) +
          '.</span>' +
          '<span class="name">' +
          safeName +
          '</span>' +
          '<button type="button" class="remove-btn" aria-label="Remove ' +
          safeName +
          '">✕</button>' +
          '</li>'
        );
      })
      .join('');
  }

  function updateActions() {
    const n = selectedList.length;
    countEl.textContent = n + ' selected';
    actions.style.display = n || allRows.length ? '' : 'none';
    if (editOrderBtn) editOrderBtn.style.display = n ? '' : 'none';
    if (n) {
      const url =
        location.origin + '/custom_gauges.php?h=' + selectedList.join(',');
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
    const needed = names.filter((n) => !stateCache.has(n));
    if (needed.length) {
      try {
        const url =
          '/gauge_picker.php?ajax=1&states=' +
          encodeURIComponent(needed.join(','));
        const resp = await fetch(url);
        const rows = await resp.json();
        // The endpoint returns rows for ALL needed states in one shot. To
        // partition by state, we group on the row's state field rather than
        // re-querying per state.
        const byState = new Map();
        for (const n of needed) byState.set(n, []);
        for (const r of rows) {
          // A border gauge's state is a comma list ('Oregon,Washington');
          // push it into every requested-state bucket so it appears under
          // each. buildAllRows() dedupes by id, so the table shows it once.
          for (const sname of String(r.state || '').split(',')) {
            const list = byState.get(sname.trim());
            if (list) list.push(r);
          }
          byHandle.set(r.h, r);
        }
        for (const n of needed) {
          stateCache.set(n, byState.get(n) || []);
        }
      } catch {
        return; // keep stale data on network error
      }
    }
    buildAllRows();
    search.disabled = false;
    renderTable();
  }

  // Drag & drop
  let dragEl = null,
    placeholder = null;

  ol.addEventListener('pointerdown', function (e) {
    if (dragEl) return;
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

  ol.addEventListener('pointermove', function (e) {
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
      selectedList = Array.from(ol.querySelectorAll('li[data-h]')).map(
        (li) => li.dataset.h,
      );
      selectedSet.clear();
      for (const id of selectedList) selectedSet.add(id);
      renderSelected();
      updateActions();
    }
  }
  ol.addEventListener('pointerup', function () {
    endDrag(true);
  });
  ol.addEventListener('pointercancel', function () {
    endDrag(false);
  });

  ol.addEventListener('keydown', function (e) {
    const li = e.target.closest('li[data-h]');
    if (!li) return;
    const id = li.dataset.h;
    const idx = selectedList.indexOf(id);
    if (idx < 0) return;
    let action = null;
    if (e.key === 'ArrowUp' && idx > 0) {
      move(idx, idx - 1);
      action = 'move';
    } else if (e.key === 'ArrowDown' && idx < selectedList.length - 1) {
      move(idx, idx + 1);
      action = 'move';
    } else if (e.key === 'Delete' || e.key === 'Backspace') {
      remove(id);
      syncCheckbox(id, false);
      action = 'remove';
    } else {
      return;
    }
    e.preventDefault();
    renderSelected();
    if (action === 'move') {
      const target = ol.querySelector('li[data-h="' + id + '"]');
      if (target) target.focus();
    }
    updateActions();
  });

  ol.addEventListener('click', function (e) {
    const btn = e.target.closest('.remove-btn');
    if (!btn) return;
    const li = btn.closest('li[data-h]');
    if (!li) return;
    const id = li.dataset.h;
    remove(id);
    syncCheckbox(id, false);
    renderSelected();
    updateActions();
  });

  tbody.addEventListener('change', function (e) {
    if (e.target.type !== 'checkbox') return;
    const id = e.target.dataset.h;
    if (e.target.checked) add(id);
    else remove(id);
    renderSelected();
    updateActions();
  });

  selectAll.addEventListener('change', function () {
    const checked = selectAll.checked;
    const q = search.value.toLowerCase();
    tbody.querySelectorAll('tr:not([hidden])').forEach(function (tr) {
      const cb = tr.querySelector('input[data-h]');
      if (!cb) return;
      const id = cb.dataset.h;
      const label = tr
        .querySelector('td:nth-child(2)')
        .textContent.toLowerCase();
      if (q && !label.includes(q)) return;
      cb.checked = checked;
      if (checked) add(id);
      else remove(id);
    });
    renderSelected();
    updateActions();
  });

  if (clearBtn) {
    clearBtn.addEventListener('click', function () {
      selectedList = [];
      selectedSet.clear();
      tbody.querySelectorAll('input[data-h]').forEach((cb) => {
        cb.checked = false;
      });
      renderSelected();
      updateActions();
    });
  }

  if (editOrderBtn) {
    editOrderBtn.addEventListener('click', function () {
      const section = document.querySelector('.selected-section');
      if (!section) return;
      section.scrollIntoView({ behavior: 'smooth', block: 'start' });
      const first = ol.querySelector('li[data-h]');
      if (first) first.focus({ preventScroll: true });
    });
  }

  copyBtn.addEventListener('click', function () {
    const url = viewLink.href;
    if (!url || url === '#') return;
    navigator.clipboard
      .writeText(url)
      .then(function () {
        copiedEl.style.display = '';
        setTimeout(function () {
          copiedEl.style.display = 'none';
        }, 2000);
      })
      .catch(function () {});
  });

  pills.addEventListener('change', loadStates);
  search.addEventListener('input', renderTable);

  function readHandlesFromUrl() {
    const m = location.search.match(/[?&]h=([^&]+)/);
    if (!m) return [];
    return m[1].split(',').filter((s) => /^[0-9A-Za-z]+$/.test(s));
  }
  const initialHandles = readHandlesFromUrl();
  if (initialHandles.length) {
    initialHandles.forEach(add);
    pills.querySelectorAll('input[type=checkbox]').forEach((cb) => {
      cb.checked = true;
    });
  }
  renderSelected();
  if (checkedStates().length) {
    loadStates().then(renderSelected);
  }
})();
