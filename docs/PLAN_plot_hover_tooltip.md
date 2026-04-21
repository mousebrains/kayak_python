# Plan — Hover tooltip on description.php plots

> **Cross-check:** Plan drafted 2026-04-20 against `main` at commit `6c3d199` (dual-axis plot just shipped). A second Claude session should re-run the read-only commands in **§Reproduce** below and confirm the findings before any edits land.
>
> Dates are absolute. References are `file:line` against `main` at the time of writing.
>
> **Last verified against `main`:** commit `6c3d199` (2026-04-20). Post-pull check confirmed: `php/includes/svg_plot.php` (411 lines, contains `generate_svg_plot` and `generate_rating_dual_plot`), `php/description.php` (589 lines), `static/*.js` holds the existing external-JS conventions (`filters.js`, `reach-map.js`, `levels.js`), nginx CSP at `/etc/nginx/snippets/security-headers.conf:17` is `script-src 'self'; style-src 'self' 'unsafe-inline'`.

## 1. Goal

Let a user hover (or tap, on touch) anywhere along a plotted series on `/description.php?id=...` and see a small popup with the timestamp and value(s) of the nearest data point. Applies to:

- **Single-line plots** (`generate_svg_plot`) — flow, inflow, gauge, temperature.
- **Dual-axis plot** (`generate_rating_dual_plot`) — shows both flow (as drawn) and the rated gauge-height (derived via the same rating lookup the right axis uses).

Out of scope:

- Build-time sparklines on `/OR.html` etc. — they're too small for meaningful hover.
- `/data.php` — already a data-inspector table.
- `plot.php` / `api.php` — no caller uses hover.

## 2. User-confirmed decisions (need your input before coding)

1. **Dual-plot tooltip content.** Recommend `12,500 CFS · 6.2 ft` (flow as plotted + gauge via rating curve). Alternative: flow only. — *Pick one.*
2. **Change/hr indicator.** Redundant with the readings table above the plots. Recommend: **no**. — *Confirm skip.*
3. **Crosshair line.** Draw a thin dashed vertical line at cursor x to anchor the tooltip. Recommend: **yes**. — *Confirm yes/no.*
4. **Downsampled vs. raw points.** Recommend LTTB-downsampled (~4.5 KB/plot in `data-series`, matches what's drawn). Raw would be up to ~50 KB on busy gauges over 10 days. — *Confirm LTTB.*
5. **Touch UX.** Tap to show, tap outside to dismiss. Long-press for "pin" isn't needed. — *Confirm.*
6. **Scope of load.** Include the hover script on every page (no-op if no `[data-series]`) via `footer.php`, rather than gating per-page. Recommend **yes** — simpler and matches how `levels.js` loads. — *Confirm.*

## 3. Architecture

Three moving parts, kept loosely coupled by a `data-*` contract:

```
┌─────────────────────────────────────────────────────┐
│  php/includes/svg_plot.php                          │
│  emits <svg data-series='{points: ..., ...}'>       │
└───────────────────┬─────────────────────────────────┘
                    │ (HTML contract)
┌───────────────────▼─────────────────────────────────┐
│  static/plot-hover.js   (new, ~80 lines)            │
│  scans svg[data-series], handles pointer events,    │
│  creates crosshair + marker + tooltip div           │
└───────────────────┬─────────────────────────────────┘
                    │ (DOM styling)
┌───────────────────▼─────────────────────────────────┐
│  src/kayak/web/static/style.css                     │
│  .plot-container (relative positioning)             │
│  .plot-tooltip, .plot-crosshair, .plot-marker       │
└─────────────────────────────────────────────────────┘
```

The JS is a standalone progressive enhancement: no-op without JS, no ARIA live region, no server round-trip. Core information is still in the readings table above the plots.

## 4. Code change summary

### 4.A. `php/includes/svg_plot.php`

Add a JSON `data-series` attribute to each emitted `<svg>`. The `generate_rating_dual_plot` variant carries the rating lookup too.

**Single plot payload:**

```json
{
  "points": [[1729400000, 12345], [1729403600, 12500], ...],
  "label": "Flow",
  "unit": "CFS",
  "decimals": 0,
  "kind": "single"
}
```

**Dual plot payload:**

```json
{
  "points": [[ts, flow_cfs], ...],
  "label": "Flow",
  "unit": "CFS",
  "decimals": 0,
  "rating": [[3.0, 100.0], [4.0, 200.0], ...],
  "kind": "dual"
}
```

Implementation notes:

- Emit the LTTB-downsampled pairs (same array used for the polyline). Attach the attribute after downsampling so tooltip points always match the drawn curve.
- HTML-escape via `htmlspecialchars($json, ENT_QUOTES, 'UTF-8')` before interpolation (matches the pattern at `php/reach.php:293` and `php/description.php:410`).
- Add a small helper `_plot_decimals(string $y_label): int` that maps `"Flow (CFS)"` / `"Inflow (CFS)"` → 0 and `"Gage Height (Ft)"` / `"Temperature (F)"` → 1. Keeps the rounding consistent with `nice_axis`'s y-step-driven decimals and with the readings table above.
- Plot margins (`ml`, `mr`, `mt`, `mb`) are known constants (80/20/30/45 single; 80/80/30/45 dual). Emit them as part of the data payload so the JS doesn't need to hard-code them — keeps PHP authoritative:

```json
{ "margins": {"ml": 80, "mr": 20, "mt": 30, "mb": 45, "w": 800, "h": 350}, ... }
```

### 4.B. New `static/plot-hover.js` (~80 lines, IIFE, ES5 style)

HTML contract:

- Selector: `.plot-container svg[data-series]`
- Attribute: `data-series` = JSON blob (schema above)

Behavior:

1. On `DOMContentLoaded`, scan for every matching SVG. Per SVG:
   - Parse `data-series`.
   - Attach `pointermove`, `pointerleave`, `touchstart` handlers to the SVG.
   - Lazily create a sibling `<div class="plot-tooltip" hidden>` inside the `.plot-container`.
   - Lazily create a hidden `<line class="plot-crosshair">` and `<circle class="plot-marker-flow">` inside the SVG.

2. On pointer move:
   - Convert `clientX` → SVG coords (`svg.createSVGPoint()` + inverse CTM) → data-space timestamp using `ml`/`mr` margins and the first/last point's timestamps.
   - Binary-search the points array for the index with the closest timestamp. O(log n).
   - Position marker at that point's (px, py). Position crosshair along the same x.
   - Render tooltip content:
     - Timestamp: `4/20 14:30` (or `4/20` if the plot spans > 3 days, matching the x-axis label rule at `svg_plot.php:122`).
     - Single plot: `12,500 CFS`
     - Dual plot: `12,500 CFS · 6.2 ft` (rated gauge computed by a JS port of `rate_flow_to_gauge` — ~10 lines, tested in §6).
   - Position the tooltip in the `.plot-container` near the marker; clamp to container bounds.

3. On pointer leave / outside-touch: hide tooltip, crosshair, marker.

Style: match `filters.js` / `reach-map.js` — IIFE, `var`, no transpilation, no external deps. Fail silently if `JSON.parse` throws.

### 4.C. `src/kayak/web/static/style.css`

Add (~20 lines):

```css
.plot-container { position: relative; }
.plot-tooltip {
    position: absolute;
    pointer-events: none;
    background: rgba(255, 255, 255, 0.95);
    border: 1px solid #888;
    padding: 4px 8px;
    font: 12px/1.4 system-ui, sans-serif;
    border-radius: 3px;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
    white-space: nowrap;
    z-index: 1000;
}
.plot-tooltip[hidden] { display: none; }
.plot-crosshair { stroke: #888; stroke-width: 0.5; stroke-dasharray: 3,3; pointer-events: none; }
.plot-marker-flow { fill: #2060A0; stroke: #fff; stroke-width: 1; pointer-events: none; }
.plot-marker-gauge { fill: #C04020; stroke: #fff; stroke-width: 1; pointer-events: none; }
```

`style.css` is content-hashed and inlined by `header.php` — no template change needed; the next build picks up the new bytes.

### 4.D. `php/includes/footer.php`

Add one line next to the existing `levels.js` include:

```php
<script src="/static/plot-hover.js" defer></script>
```

The script is a no-op on pages without `svg[data-series]`, so loading it globally is safe and matches how `levels.js` is loaded.

### 4.E. `tests/php/SvgPlotTest.php` (+~30 lines)

Add two tests:

1. `test_generate_svg_plot_emits_data_series()` — assert the output contains `data-series="..."`, JSON-parse the attribute, assert it has `points` (non-empty), `label`, `decimals`, `kind === "single"`, and `margins`.
2. `test_generate_rating_dual_plot_emits_data_series_with_rating()` — same plus `kind === "dual"` and `rating` array non-empty.

No JS test harness in this repo (none exists; `filters.js`, `reach-map.js` are manually verified). Port of `rate_flow_to_gauge` in JS is tiny (~10 lines) and covered by the manual smoke tests below.

### 4.F. Out of scope (explicitly)

- Build-time sparklines (`levels build`): unchanged. They're tiny and the static HTML has no JS.
- Keyboard navigation / ARIA announcement: progressive enhancement only; users without pointer devices still have the readings table and the `/data.php` link in-page.
- Dark-mode theming: the tooltip uses site chrome colors; no `prefers-color-scheme` branching.

## 5. Edge cases & decisions

| Case | Behavior |
|---|---|
| Pointer outside the plot area (over margins/title) | Tooltip hidden. |
| Between two LTTB-downsampled points | Show nearest by timestamp (no interpolation — matches the line's visible vertices). |
| Flow polyline hits a flat rating region | Flow tooltip value is unchanged; gauge may step between nearby bins. Acceptable — the right axis has the same behavior. |
| Touch device | `touchstart` shows tooltip at touch point; next `pointerdown` outside the SVG hides it. |
| Tooltip would clip the right edge of `.plot-container` | Flip to the left of the marker. |
| Tooltip would clip the top | Flip below the marker. |
| Narrow viewport (mobile) | Same clamping. Margins unchanged. |
| Zero data points (empty-SVG fallback) | No `data-series` emitted by PHP → JS no-op. |
| User zooms the page | `createSVGPoint` + CTM inverse handles scaling correctly. |
| User has JS disabled | Plot still renders; no tooltip. No regression. |
| CSP violation (`script-src 'self'`) | File lives at `/static/plot-hover.js` — same-origin, allowed. |

## 6. Verification

1. `php -l php/includes/svg_plot.php php/includes/footer.php`.
2. Run existing `SvgPlotTest` + the two new `data-series` tests. (CI; local via `/tmp/composer install` if dev runs.)
3. `SQLITE_PATH=/home/pat/DB/kayak.db php -S localhost:8765 -t /home/pat/kayak/public_html` and:
   - Load `/description.php?id=4988`. Hover across the flow line; tooltip shows `MM/DD HH:MM — 12,500 CFS · 6.2 ft` and tracks the cursor.
   - Check DevTools → Console: no errors, no CSP violations.
   - Check DevTools → Network: only one new asset (`/static/plot-hover.js`, expect < 2 KB gzipped).
   - Hover near each end of the x-axis: tooltip pins to the first / last point without overflowing.
4. Load `/description.php?id=1` (flow-only) — tooltip shows `12,500 CFS`, no `· ft` suffix.
5. Load `/description.php?id=5491` (gauge + temp, no flow) — each plot tooltip shows its own label.
6. Use Chromium DevTools → Toggle device toolbar → iPhone 14: tap the flow line, tap elsewhere to dismiss.
7. `levels build` and confirm the new snapshot contains `/static/plot-hover.js` and the updated `style.css`. Hit `levels-test.wkcc.org/description.php?id=4988` for an end-to-end check.

## 7. Estimated diff

| File | +lines | −lines |
|---|---:|---:|
| `php/includes/svg_plot.php` | ~40 | 0 |
| `static/plot-hover.js` (new) | ~80 | — |
| `src/kayak/web/static/style.css` | ~20 | 0 |
| `php/includes/footer.php` | 1 | 0 |
| `tests/php/SvgPlotTest.php` | ~30 | 0 |

Total: ~170 additions, 0 deletions.

## 8. Reproduce

```sh
# Verify CSP: same-origin script-src lets /static/plot-hover.js load.
grep -h script-src /etc/nginx/snippets/security-headers*.conf
# expected: "script-src 'self';" (or with hcaptcha exceptions on forms pages)

# Verify where external JS is deployed to.
ls /home/pat/kayak/static/*.js
# expected: filters.js, levels.js, map.js, picker.js, reach-map.js, search-map.js, sw.js

# Verify data-* attribute convention is already used in PHP.
grep -n 'data-[a-z]*="' /home/pat/kayak/php/description.php /home/pat/kayak/php/reach.php | head
# expected: existing data-points / data-track / data-reaches attributes on map divs

# Verify plot-container is the stable wrapper.
grep -n 'plot-container' /home/pat/kayak/php/description.php /home/pat/kayak/php/includes/svg_plot.php
# expected: description.php echoes '<div class="plot-container">' around each plot

# Verify LTTB payload size.
# 200 points × ~22 bytes/pt JSON + ~10% attribute-escape overhead ≈ 5 KB per plot.
# Dual plot adds rating (~50 pairs × ~15 bytes = 0.75 KB). Negligible.
```
