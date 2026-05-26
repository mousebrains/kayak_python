// /_internal/ sortable-table glue. CSP-safe: no inline handlers, no eval.
// Attaches a click-to-sort behavior to every <table> inside <details.collapsible>;
// click a column heading to sort ascending, click again for descending.
//
// The page tag should be cache-busted with ?v=<mtime> because nginx serves
// /static/ as immutable max-age=1y. Without that the browser pins the
// first download for a year and ignores selector updates.
//
// Sort kind is inferred per column from the first body row:
//   - "3.2 days", "12 GB" etc.    → leading number, numeric sort
//   - "2026-05-21 14:00"          → ISO-ish date string, lexicographic sort
//   - everything else             → case-insensitive lexicographic
// `<code>…</code>` wrappers (used for IPs in the per-IP table) are stripped
// for comparison.

(function () {
  function cellText(cell) {
    return (cell.textContent || '').trim();
  }

  function numeric(value) {
    // Pull the leading number ("1650", "3.2", "-0.5"); return NaN otherwise.
    const m = value.replace(/,/g, '').match(/^-?\d+(?:\.\d+)?/);
    return m ? parseFloat(m[0]) : NaN;
  }

  function inferKind(col, table) {
    const rows = table.tBodies[0]?.rows;
    if (!rows?.length) return 'string';
    const sample = cellText(rows[0].cells[col]);
    if (!isNaN(numeric(sample))) return 'number';
    return 'string';
  }

  function compareFactory(kind) {
    if (kind === 'number') {
      return function (a, b) {
        const an = numeric(a);
        const bn = numeric(b);
        if (isNaN(an) && isNaN(bn)) return 0;
        if (isNaN(an)) return 1;
        if (isNaN(bn)) return -1;
        return an - bn;
      };
    }
    return function (a, b) {
      return a.toLowerCase().localeCompare(b.toLowerCase());
    };
  }

  function setIndicator(th, dir) {
    const existing = th.querySelector('.sort-indicator');
    if (existing) existing.remove();
    if (!dir) return;
    const span = document.createElement('span');
    span.className = 'sort-indicator';
    span.textContent = dir === 'asc' ? ' ▲' : ' ▼';
    th.appendChild(span);
  }

  function makeSortable(table) {
    if (!table.tHead || !table.tBodies[0]) return;
    const headers = table.tHead.rows[0].cells;
    for (let i = 0; i < headers.length; i++) {
      (function (col) {
        const th = headers[col];
        th.style.cursor = 'pointer';
        th.title = 'Click to sort';
        th.addEventListener('click', function () {
          // Toggle direction; clear other headers' indicators.
          const dir = th.dataset.sortDir === 'asc' ? 'desc' : 'asc';
          for (let j = 0; j < headers.length; j++) {
            headers[j].dataset.sortDir = '';
            setIndicator(headers[j], '');
          }
          th.dataset.sortDir = dir;
          setIndicator(th, dir);

          const kind = inferKind(col, table);
          const cmp = compareFactory(kind);
          const rows = Array.prototype.slice.call(table.tBodies[0].rows);
          rows.sort(function (a, b) {
            const av = cellText(a.cells[col]);
            const bv = cellText(b.cells[col]);
            const r = cmp(av, bv);
            return dir === 'asc' ? r : -r;
          });
          const tbody = table.tBodies[0];
          for (let k = 0; k < rows.length; k++) tbody.appendChild(rows[k]);
        });
      })(i);
    }
  }

  function init() {
    const tables = document.querySelectorAll('details.collapsible table');
    for (let t = 0; t < tables.length; t++) makeSortable(tables[t]);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
