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
    return (cell.textContent || "").trim();
  }

  function numeric(value) {
    // Pull the leading number ("1650", "3.2", "-0.5"); return NaN otherwise.
    var m = value.replace(/,/g, "").match(/^-?\d+(?:\.\d+)?/);
    return m ? parseFloat(m[0]) : NaN;
  }

  function inferKind(col, table) {
    var rows = table.tBodies[0] && table.tBodies[0].rows;
    if (!rows || !rows.length) return "string";
    var sample = cellText(rows[0].cells[col]);
    if (!isNaN(numeric(sample))) return "number";
    return "string";
  }

  function compareFactory(kind) {
    if (kind === "number") {
      return function (a, b) {
        var an = numeric(a);
        var bn = numeric(b);
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
    var existing = th.querySelector(".sort-indicator");
    if (existing) existing.remove();
    if (!dir) return;
    var span = document.createElement("span");
    span.className = "sort-indicator";
    span.textContent = dir === "asc" ? " ▲" : " ▼";
    th.appendChild(span);
  }

  function makeSortable(table) {
    if (!table.tHead || !table.tBodies[0]) return;
    var headers = table.tHead.rows[0].cells;
    for (var i = 0; i < headers.length; i++) {
      (function (col) {
        var th = headers[col];
        th.style.cursor = "pointer";
        th.title = "Click to sort";
        th.addEventListener("click", function () {
          // Toggle direction; clear other headers' indicators.
          var dir = th.dataset.sortDir === "asc" ? "desc" : "asc";
          for (var j = 0; j < headers.length; j++) {
            headers[j].dataset.sortDir = "";
            setIndicator(headers[j], "");
          }
          th.dataset.sortDir = dir;
          setIndicator(th, dir);

          var kind = inferKind(col, table);
          var cmp = compareFactory(kind);
          var rows = Array.prototype.slice.call(table.tBodies[0].rows);
          rows.sort(function (a, b) {
            var av = cellText(a.cells[col]);
            var bv = cellText(b.cells[col]);
            var r = cmp(av, bv);
            return dir === "asc" ? r : -r;
          });
          var tbody = table.tBodies[0];
          for (var k = 0; k < rows.length; k++) tbody.appendChild(rows[k]);
        });
      })(i);
    }
  }

  function init() {
    var tables = document.querySelectorAll("details.collapsible table");
    for (var t = 0; t < tables.length; t++) makeSortable(tables[t]);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
