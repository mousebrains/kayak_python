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
 *   2. Table rows with data-state, data-basin, data-status, data-tier
 *      attributes (hyphenated → .dataset.state etc.).
 *
 * Behaviour:
 *   - Filter-bar starts hidden. We inject a "Filter" link into the page
 *     nav bar; clicking it toggles visibility.
 *   - Each group has All/None buttons that check/uncheck every pill in
 *     that group (without touching other groups).
 *   - URL hash round-trips the non-default filter state with short keys
 *     (st=state, b=basin, s=status, c=class).
 *   - Rows that fail the predicate get the [hidden] attribute.
 */
(function(){
  var HASH_KEYS = {state:'st', basin:'b', status:'s', tier:'c'};
  var DESKTOP_QUERY = '(min-width:641px)';

  function readHash(){
    var out = {};
    var h = (location.hash || '').replace(/^#/, '');
    if (!h) return out;
    h.split('&').forEach(function(kv){
      var eq = kv.indexOf('=');
      if (eq < 0) return;
      var k = kv.slice(0, eq), v = kv.slice(eq + 1);
      out[k] = v === '' ? [] : decodeURIComponent(v).split(',').filter(Boolean);
    });
    return out;
  }

  function writeHash(groups){
    var parts = [];
    groups.forEach(function(g){
      var all = g.pills.length;
      var checked = g.pills.filter(function(p){return p.input.checked}).length;
      if (checked === all) return;
      var vals = g.pills.filter(function(p){return p.input.checked}).map(function(p){return p.input.value});
      parts.push(HASH_KEYS[g.key] + '=' + vals.map(encodeURIComponent).join(','));
    });
    var newHash = parts.length ? ('#' + parts.join('&')) : '';
    if (newHash !== location.hash) {
      history.replaceState(null, '', location.pathname + location.search + newHash);
    }
  }

  function applyHash(groups){
    var hash = readHash();
    groups.forEach(function(g){
      var key = HASH_KEYS[g.key];
      if (!(key in hash)) return;
      var want = new Set(hash[key]);
      g.pills.forEach(function(p){
        p.input.checked = want.has(p.input.value);
      });
    });
  }

  function collectGroups(barEl){
    var groups = [];
    barEl.querySelectorAll('.filter-pills').forEach(function(container){
      var key = container.dataset.group;
      if (!key) return;
      var pills = Array.from(container.querySelectorAll('input[type=checkbox]')).map(function(input){
        return {input: input, label: input.closest('label')};
      });
      groups.push({key: key, container: container, pills: pills,
                   summary: container.parentElement.querySelector('summary'),
                   splitCSV: container.dataset.split === 'csv'});
    });
    return groups;
  }

  function matches(row, groups){
    for (var i = 0; i < groups.length; i++) {
      var g = groups[i];
      var raw = row.dataset[g.key] || '';
      var values = g.splitCSV ? (raw ? raw.split(',') : ['?']) : [raw];
      var anyChecked = false;
      for (var j = 0; j < g.pills.length; j++) {
        var p = g.pills[j];
        if (!p.input.checked) continue;
        if (values.indexOf(p.input.value) !== -1) { anyChecked = true; break; }
      }
      if (!anyChecked) return false;
    }
    return true;
  }

  function refilter(ctx){
    var visible = 0;
    ctx.rows.forEach(function(row){
      var show = matches(row, ctx.groups);
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
    var nav = document.querySelector('header nav');
    if (!nav) return;
    if (nav.querySelector('.filter-toggle-nav')) return;
    var a = document.createElement('a');
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
    });
    nav.appendChild(a);
  }

  function wireAllNone(group, ctx){
    var container = group.container;
    container.addEventListener('click', function(e){
      var t = e.target;
      if (!(t instanceof HTMLElement)) return;
      if (t.matches('[data-all]')) {
        e.preventDefault();
        group.pills.forEach(function(p){ p.input.checked = true; });
        refilter(ctx);
      } else if (t.matches('[data-none]')) {
        e.preventDefault();
        group.pills.forEach(function(p){ p.input.checked = false; });
        refilter(ctx);
      }
    });
  }

  function init(opts){
    var bar = opts && opts.barContainer || document.getElementById('filter-bar');
    if (!bar) return null;
    var tbody = opts && opts.rowsContainer || document.querySelector('table.levels tbody');
    if (!tbody) return null;

    var rows = Array.from(tbody.querySelectorAll('tr[data-state], tr[data-basin], tr[data-status], tr[data-tier]'));
    var groups = collectGroups(bar);
    var countEl = bar.querySelector('.fb-count');
    var resetBtn = bar.querySelector('.fb-reset');

    var ctx = {
      rows: rows,
      groups: groups,
      countEl: countEl,
      onChange: opts && opts.onChange,
    };

    applyHash(groups);

    groups.forEach(function(g){
      g.container.addEventListener('change', function(e){
        if (e.target && e.target.type === 'checkbox') refilter(ctx);
      });
      wireAllNone(g, ctx);
    });

    if (resetBtn) {
      resetBtn.addEventListener('click', function(){
        groups.forEach(function(g){
          g.pills.forEach(function(p){ p.input.checked = true; });
        });
        refilter(ctx);
      });
    }

    // Open every group on desktop (mobile users see compact summaries only).
    if (window.matchMedia && window.matchMedia(DESKTOP_QUERY).matches) {
      bar.querySelectorAll('details.filter-group').forEach(function(d){ d.open = true; });
    }

    // Bar default-hidden; nav gets a "Filter" toggle. If the URL has
    // explicit filter state, auto-reveal so users see why rows are missing.
    var hashHas = Object.keys(readHash()).length > 0;
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
