/* Shared pill-based filter bar for levels tables.
 *
 * Fetches nothing; reads two HTML contracts:
 *
 *   1. A filter-bar container (default-hidden) with per-group <details>
 *      blocks. Each group has a .filter-pills div with data-group="..."
 *      and optional data-split="csv" for multi-valued rows (class tiers).
 *      Inside each .filter-pills, an .fg-toggle node carries the
 *      All/None buttons; a .filter-meta block carries the count + reset.
 *
 *      The basin group (data-group="huc8") nests <details class="filter-subgroup">
 *      blocks per HUC6, each with a parent checkbox carrying data-huc6 and
 *      child HUC8 pills inside .filter-pills-sub. Parents are visual-only —
 *      they bulk-toggle their children but are excluded from match logic.
 *
 *   2. Table rows with data-state, data-basin, data-huc8, data-status,
 *      data-tier attributes (hyphenated → .dataset.state etc.).
 *      The basin filter matches against data-huc8 (8-digit code), not
 *      data-basin (which stays for display + PHP back-compat).
 *
 * Behaviour:
 *   - Filter-bar starts hidden. We inject a "Filter" link into the page
 *     nav bar; clicking it toggles visibility.
 *   - Each group has All/None buttons that check/uncheck every pill in
 *     that group (without touching other groups).
 *   - URL hash round-trips the non-default filter state with short keys
 *     (st=state, b=basin (now HUC8 codes), s=status, c=class).
 *   - Rows that fail the predicate get the [hidden] attribute.
 */
(function(){
  'use strict';
  const HASH_KEYS = {state:'st', huc8:'b', status:'s', tier:'c'};
  const DESKTOP_QUERY = '(min-width:641px)';

  function readHash(){
    const out = {};
    const h = (location.hash || '').replace(/^#/, '');
    if (!h) return out;
    h.split('&').forEach(function(kv){
      const eq = kv.indexOf('=');
      if (eq < 0) return;
      const k = kv.slice(0, eq), v = kv.slice(eq + 1);
      out[k] = v === '' ? [] : decodeURIComponent(v).split(',').filter(Boolean);
    });
    return out;
  }

  function writeHash(groups){
    const parts = [];
    groups.forEach(function(g){
      const all = g.pills.length;
      const checked = g.pills.filter(function(p){return p.input.checked}).length;
      if (checked === all) return;
      const vals = g.pills.filter(function(p){return p.input.checked}).map(function(p){return p.input.value});
      parts.push(HASH_KEYS[g.key] + '=' + vals.map(encodeURIComponent).join(','));
    });
    const newHash = parts.length ? ('#' + parts.join('&')) : '';
    if (newHash !== location.hash) {
      history.replaceState(null, '', location.pathname + location.search + newHash);
    }
  }

  function applyHash(groups){
    const hash = readHash();
    groups.forEach(function(g){
      const key = HASH_KEYS[g.key];
      if (!(key in hash)) return;
      const want = new Set(hash[key]);
      g.pills.forEach(function(p){
        p.input.checked = want.has(p.input.value);
      });
    });
  }

  function collectGroups(barEl){
    const groups = [];
    barEl.querySelectorAll('.filter-pills').forEach(function(container){
      const key = container.dataset.group;
      if (!key) return;
      // Exclude HUC6 parent checkboxes — they're visual bulk-toggle handles,
      // not real filter pills (they have no `value`-bearing semantic).
      const inputs = Array.from(container.querySelectorAll('input[type=checkbox]'))
        .filter(function(i){ return !i.hasAttribute('data-huc6'); });
      const pills = inputs.map(function(input){
        return {input: input, label: input.closest('label')};
      });
      groups.push({key: key, container: container, pills: pills,
                   summary: container.parentElement.querySelector('summary'),
                   splitCSV: container.dataset.split === 'csv'});
    });
    return groups;
  }

  /* HUC6 parent checkbox <-> HUC8 child checkbox sync.
   * - Parent toggles all child huc8 pills under its <details class="filter-subgroup">
   * - Any child toggle recomputes parent's checked/indeterminate state.
   * Returns a `syncParents()` callback so callers (All/None, reset, applyHash)
   * can refresh all parent states after programmatically toggling children. */
  function wireHucHierarchy(barEl, ctx){
    const parents = Array.from(barEl.querySelectorAll('input[type=checkbox][data-huc6]'));
    const pairs = parents.map(function(parent){
      const subgroup = parent.closest('details.filter-subgroup');
      const children = subgroup
        ? Array.from(subgroup.querySelectorAll('.filter-pills-sub input[type=checkbox]'))
        : [];
      return {parent: parent, children: children};
    });

    function refreshParent(pair){
      const checked = pair.children.filter(function(x){return x.checked}).length;
      pair.parent.checked = (checked === pair.children.length);
      pair.parent.indeterminate = (checked > 0 && checked < pair.children.length);
    }
    function syncAllParents(){ pairs.forEach(refreshParent); }

    pairs.forEach(function(pair){
      pair.parent.addEventListener('change', function(){
        pair.children.forEach(function(c){ c.checked = pair.parent.checked; });
        pair.parent.indeterminate = false;
        refilter(ctx);
      });
      pair.children.forEach(function(c){
        c.addEventListener('change', function(){ refreshParent(pair); });
      });
      refreshParent(pair);
    });
    return syncAllParents;
  }

  function matches(row, groups){
    for (let i = 0; i < groups.length; i++) {
      const g = groups[i];
      const raw = row.dataset[g.key] || '';
      const values = g.splitCSV ? (raw ? raw.split(',') : ['?']) : [raw];
      let anyChecked = false;
      for (let j = 0; j < g.pills.length; j++) {
        const p = g.pills[j];
        if (!p.input.checked) continue;
        if (values.indexOf(p.input.value) !== -1) { anyChecked = true; break; }
      }
      if (!anyChecked) return false;
    }
    return true;
  }

  function refilter(ctx){
    let visible = 0;
    ctx.rows.forEach(function(row){
      const show = matches(row, ctx.groups);
      if (show) { row.removeAttribute('hidden'); visible++; }
      else row.setAttribute('hidden', '');
    });
    if (ctx.countEl) {
      ctx.countEl.textContent = visible + (visible === 1 ? ' reach' : ' reaches');
    }
    writeHash(ctx.groups);
    if (typeof ctx.onChange === 'function') ctx.onChange(visible);
  }

  function injectNavToggle(bar){
    const nav = document.querySelector('header nav');
    if (!nav) return;
    if (nav.querySelector('.filter-toggle-nav')) return;
    const a = document.createElement('a');
    a.href = '#';
    a.textContent = 'Filter';
    a.className = 'filter-toggle-nav';
    a.setAttribute('role', 'button');
    a.setAttribute('aria-controls', bar.id || 'filter-bar');
    a.setAttribute('aria-expanded', bar.hidden ? 'false' : 'true');
    a.addEventListener('click', function(e){
      e.preventDefault();
      bar.hidden = !bar.hidden;
      a.setAttribute('aria-expanded', bar.hidden ? 'false' : 'true');
      // If the user clicks Filter while scrolled down (e.g., after jumping
      // to a letter anchor), the bar expands above the viewport and looks
      // like nothing happened. Scroll it into view when revealing.
      if (!bar.hidden) bar.scrollIntoView({behavior: 'smooth', block: 'start'});
    });
    nav.appendChild(a);
  }

  function wireAllNone(group, ctx, afterToggle){
    const container = group.container;
    container.addEventListener('click', function(e){
      const t = e.target;
      if (!(t instanceof HTMLElement)) return;
      if (t.matches('[data-all]')) {
        e.preventDefault();
        group.pills.forEach(function(p){ p.input.checked = true; });
        if (afterToggle) afterToggle();
        refilter(ctx);
      } else if (t.matches('[data-none]')) {
        e.preventDefault();
        group.pills.forEach(function(p){ p.input.checked = false; });
        if (afterToggle) afterToggle();
        refilter(ctx);
      }
    });
  }

  function init(opts){
    const bar = opts?.barContainer || document.getElementById('filter-bar');
    if (!bar) return null;
    const tbody = opts?.rowsContainer || document.querySelector('table.levels tbody');
    if (!tbody) return null;

    const rows = Array.from(tbody.querySelectorAll('tr[data-state], tr[data-basin], tr[data-huc8], tr[data-status], tr[data-tier]'));
    const groups = collectGroups(bar);
    const countEl = bar.querySelector('.fb-count');
    const resetBtn = bar.querySelector('.fb-reset');

    const ctx = {
      rows: rows,
      groups: groups,
      countEl: countEl,
      onChange: opts?.onChange,
    };

    applyHash(groups);

    const syncHucParents = wireHucHierarchy(bar, ctx);
    groups.forEach(function(g){
      g.container.addEventListener('change', function(e){
        if (e.target && e.target.type === 'checkbox') refilter(ctx);
      });
      wireAllNone(g, ctx, syncHucParents);
    });
    syncHucParents();  // align parent checkboxes with whatever applyHash set

    if (resetBtn) {
      resetBtn.addEventListener('click', function(){
        groups.forEach(function(g){
          g.pills.forEach(function(p){ p.input.checked = true; });
        });
        syncHucParents();
        refilter(ctx);
      });
    }

    // Open every group on desktop (mobile users see compact summaries only).
    if (window.matchMedia?.(DESKTOP_QUERY).matches) {
      bar.querySelectorAll('details.filter-group').forEach(function(d){ d.open = true; });
    }

    // Bar default-hidden; nav gets a "Filter" toggle. If the URL has
    // explicit filter state, auto-reveal so users see why rows are missing.
    const hashHas = Object.keys(readHash()).length > 0;
    if (hashHas) bar.hidden = false;

    injectNavToggle(bar);

    refilter(ctx);
    return { refilter: function(){ refilter(ctx); } };
  }

  window.kayakFilters = { init: init };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function(){
      if (!window.kayakFilters._manualInit) init();
    });
  } else {
    if (!window.kayakFilters._manualInit) init();
  }
})();
