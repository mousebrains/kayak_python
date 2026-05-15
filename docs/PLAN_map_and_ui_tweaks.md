# Plan — Map & UI tweaks

> **Cross-check:** plan drafted 2026-05-15 against `main` at `185a33b`.
>
> **Iter log:**
> - iter 1 (2026-05-15): 6 findings — (A) `_render_custom_header` does
>   not receive the original `$ids` array (`handle_custom_levels` at
>   `custom_handler.php:39` passes only `$reaches` / `$tiers_by_reach`
>   to it at `:49`). Simpler than a signature change: rebuild the id
>   list inline from `$reaches` (which is already what the page is
>   actually displaying — naturally drops ids the DB couldn't resolve).
>   Updated Item 3. (B) Verified `r.description` is loaded by the
>   Associated-Reaches SELECT at `gauge_detail.php:163` (column
>   `r.description` in the projection). `$r['description']` is
>   directly addressable in the row template. (C) `static/map.js`
>   already uses `L.circleMarker` (`:165` for point-shape reaches,
>   `:218` for the invisible hit shape on point reaches) — the gauge
>   map's marker code will read like the existing point-reach branch,
>   reducing review surprise. (D) Gauge-side status needs the same
>   `_gauge_status_from_reaches` rollup used by `gauges.py` (otherwise
>   gauge-map status colors will disagree with the gauges.html table).
>   Added explicit instruction to reuse that helper. (E) Nav becomes
>   busier: existing "Gauges" (table) entry stays; we ADD "Gauge Map"
>   so users see two gauge entries (table + map). Worth a single line
>   call-out in the PR description. (F) `/gauge.php` validates `?id=`
>   with `FILTER_VALIDATE_INT` (`php/gauge.php:24`); the gauge-map
>   popup link `/gauge.php?id=<int>` is safe.
> - iter 2 (2026-05-15): 4 findings — (A) **Click-into-popup is
>   broken by naive mouseout-closes.** The existing reach popup
>   (`buildPopup` at `map.js:178-201`) is wrapped in
>   `<a class="reach-popup" href="/description.php?id=…">` — the
>   entire popup is one clickable link. Closing the popup on trace
>   mouseout means the user can never move cursor into the popup to
>   click it. Rewrote Item 1 mechanic: use the `popupopen` event to
>   attach `mouseenter`/`mouseleave` to the popup DOM element; only
>   close when neither the trace nor the popup is hovered, with a
>   100-150 ms grace delay on the close path. Same fix applies to
>   Item 2's gauge-map popup. (B) Mouseout handler already exists at
>   `map.js:229-233` (resets style); only need to ADD the
>   popup-close path, not create the handler. (C) Item 4 column
>   order: re-evaluated. On a per-gauge page, the user is browsing
>   reaches that share the same gauge — `Location` differentiates
>   more strongly than `River` (river is often constant or
>   near-constant across rows). Recommendation flipped to
>   **Name / Location / River / Class / Length / Status** (Location
>   right after Name, since it's the primary differentiator on this
>   page). Open-question #3 reframed. (D) Tile provider /
>   attribution / fit-bounds behavior comes for free with the
>   map.js fork — no separate decision needed for gauge-map.js.
> - iter 3 (2026-05-15): 3 findings — (A) Sampled gauge 14's reach
>   descriptions: "Big Dog", "Mile 5.5 Bridge to Two Rivers",
>   "South Fork Clackamas Falls", "June Creek Br. to Collawash
>   River", "Collawash River to Three Lynx Power Station", "Three
>   Lynx Power Station to North Fork Reservoir", "Elk Lake to NF
>   road 6380". Highly differentiated — Location column will be
>   immediately useful. (B) Of gauge 14's 7 reach rows, 4 have
>   `name = "aw_<id>"` placeholders that tell users nothing.
>   Reinforces "Location right after Name" ordering. (C) `r.basin`
>   for those 7 rows is uniformly "Clackamas" — confirms the
>   current Watershed column adds no value on this page; dropping
>   it is the right call.
> - iter 4 (2026-05-15): 4 findings — (A) The reach map **already
>   has filter pills** (Status + Class, with URL-hash persistence
>   for shareable filtered views — see `map.js:303-307` and the
>   `.map-filter` CSS injected by `_build_map_page` at
>   `shell.py:358-375`). My iter-0 claim "the reach map ships
>   without them today" was wrong. Updated Item 2 to include a
>   Status filter on the gauge map for parity. (B) `_LICENSE_META`
>   is embedded as `_meta` in both reach JSON files
>   (`geojson.py:86, 116`); gauge JSON files should follow.
>   (C) `_get_row_data()` is the canonical row-status mapper for
>   reaches; gauges already have `_gauge_status_from_reaches()` in
>   `gauges.py`. (D) `_MAP_JS_VERSION` cache-buster constant
>   (`shell.py`) — gauge-map.js needs its own analogue, e.g.
>   `_GAUGE_MAP_JS_VERSION`.
> - iter 5 (2026-05-15): 5 findings — (A) **Name-collision warning.**
>   `php/includes/gauge_map.php` already exists — it's an include for
>   **embedded mini-maps on detail pages** (`description.php`,
>   `gauge.php`, `reach.php`, `reach_search.php`) backed by
>   `static/feature-map.js`. Completely separate from the new
>   standalone gauges-overview map. URL `/gauge-map.html` (hyphen)
>   does not collide with PHP file `gauge_map.php` (underscore).
>   Plan adds an explicit naming-distinction note. (B) `_emit_sitemap()`
>   at `deploy.py:239-283` enumerates public landing URLs and
>   currently lists `/map.html` at `:257`. Must add
>   `/gauge-map.html` alongside. (C) `style.css:418` already ships
>   the `.reach-popup` CSS — the gauge-map popup can reuse it
>   verbatim, OR we rename selectors to `.feature-popup` to signal
>   shared use. Recommend: keep `.reach-popup` selector and reuse;
>   minimal churn. (D) URL convention confirmed:
>   reach map stays at `/map.html` (no rename — five other files
>   reference it), gauge map is `/gauge-map.html`. (E)
>   `static/feature-map.js` is the detail-page minimap script (NOT
>   the same as `static/map.js`); my Items 1-2 don't touch it.
> - iter 6 (2026-05-15): 4 findings — (A)
>   `GaugeIntegrationTest.php:190` asserts the string "Estacada, OR"
>   (the gauge's own Location field, not the table column header).
>   No "Watershed" string is asserted anywhere. Item 4 will not break
>   existing tests; still run `composer test` post-change.
>   (B) `CustomIntegrationTest.php` tests inbound `/custom.php`
>   handling on missing/invalid ids; outbound "Edit selection" link
>   is not asserted. Item 3 won't break existing tests.
>   (C) Item 2 PR sizing: keep all 5 sub-pieces (2a-2e) in a single
>   PR — the JSON files are useless without the page that consumes
>   them, and the page is useless without the data. Splitting would
>   produce two un-shippable intermediate states. Items 1, 3, 4
>   remain independent PRs. (D) Performance: ~150 visible gauge
>   markers (after expiry filter on the live 192-gauge dataset).
>   Leaflet handles this trivially; no clustering needed. Plus
>   mobile tap-target — current `circleMarker` radius 7-8 px is
>   under the 44 px tap-target minimum. Mitigation: add a transparent
>   larger hit shape (e.g., 14 px), same pattern map.js uses for
>   point reaches at `:218` (`HIT_POINT`).
> - iter 7 (2026-05-15): Item 5 added — scroll-position indicators
>   for horizontally-scrollable nav strips. 4 findings during scope
>   recon. (A) **An indicator already exists** but is incomplete:
>   `style.css:70-71` applies an unconditional right-edge fade via
>   `mask-image` when `<768px`. It does NOT (i) update with scroll
>   position (still shows when scrolled to end), (ii) show a left
>   fade when scrolled away from the start, (iii) trigger above the
>   breakpoint when the nav happens to overflow anyway. Need
>   scroll-position-aware behavior, not a new feature from scratch.
>   (B) Two scrollable strips per page: main state nav (`<nav>` in
>   header) AND letter nav (`.letter-nav` A-Z jump links). Both want
>   the indicator. (C) Filter pills in picker/custom use
>   `flex-wrap: wrap` (`style.css:293`) — they don't scroll, so no
>   indicator needed there. (D) **Synergy with Item 2:** adding the
>   "Gauge Map" nav entry pushes the main nav wider, making it
>   overflow earlier (i.e., on slightly wider viewports than today).
>   The improved scroll indicator becomes more useful right when
>   Item 2 lands. Suggested PR order: Item 5 before Item 2.
> - iter 8 (2026-05-15, stopping): 3 findings cross-checking Item 5
>   against the existing items. (A) Item 5's JS bootstrap must be
>   loaded on EVERY page, not just map pages. PHP pages via
>   `header.php`; built pages need the script tag injected by each
>   builder (`_build_map_page`, `_build_state_page`, `_build_gauges_page`,
>   `_build_gauge_map_page` if added). Easiest: have the builder add
>   the `<script>` in the shared shell function once. (B) Item 2's
>   nav additions inherit the indicator for free — no extra work
>   needed beyond adding `data-scroll-indicate` to the existing
>   `<nav>` elements (already needed for Item 5). (C) First-paint
>   "flicker": before JS runs, attributes are absent and the new
>   CSS shows no fade. Today's user sees the right fade pre-JS;
>   under the new behavior they see nothing pre-JS then a fade
>   appears. Subtle regression for slow connections. Mitigation:
>   keep a CSS fallback rule `header > nav:not([data-scroll-indicator-active])`
>   that shows the old always-on right fade until JS sets the
>   `data-scroll-indicator-active` flag. Or: accept the brief flicker.
>   Recommendation: accept the flicker — JS loads early via `defer`
>   on a tiny ~1 KB file; difference is sub-100 ms.
> - iter 9 (2026-05-15, stopping): 3 findings — (A) Item 1 grace
>   timing was inconsistent: "100-150 ms" in iter-2 log, "150 ms" in
>   body, stale "75 ms" reference in open question 5. Normalized to
>   150 ms throughout; open question reframed as a tuning band
>   (100-200 ms). (B) Body "Files affected" for Item 1 said "4-6
>   lines" — outdated relative to the popupopen + closeTimer code in
>   the mechanic. Updated to ~25 lines. (C) Scope inventory had no
>   CSS section; added one citing `style.css:48-77` for Item 5.
>   Added open question 6 for Item 5's first-paint trade-off.
>   Convergence (iter 1-9): 6 → 4 → 3 → 4 → 5 → 4 → 4 → 3 → 3.
>   Stopping; remaining open questions are user-facing decisions,
>   not implementation gaps.
> - iter 10 (2026-05-15, decisions): user accepted all 6 v1
>   recommendations. Converted "Open questions" section to
>   "Decisions" with values baked in. Also fixed a stale "filter
>   pills — follow-up" entry that should have been updated by
>   iter-4 (the body and testing sections already said "Status
>   filter for parity").
> - iter 11 (2026-05-15, Item 2 redesign): user decided to **merge
>   gauges into the existing reach map** rather than build a
>   separate `/gauge-map.html`. Mitigation A+C accepted: a "Show
>   gauges" toggle in the existing filter panel (default ON) AND
>   zoom-graded marker sizes (3 px at low zoom, 7 px at high zoom).
>   Reworked Item 2 from a 5-piece (2a-2e) buildout into a 3-piece
>   (2a JSON pipeline + 2b build wiring unchanged; 2c rebranded as
>   "extend map.js + filter panel"). Items dropped: separate
>   gauge-map.html page, new `_build_gauge_map_page()`, new
>   `static/gauge-map.js`, sitemap entry, nav rename ("Map"
>   stays — no "Reach Map" / "Gauge Map" split). Cascading
>   updates: (A) the "Map" nav entry doesn't change label or
>   active key — `php/includes/header.php:87` and
>   `shell.py:_build_nav()` are no longer touched by Item 2.
>   (B) Item 5's iter-7 (D) "Item 5 should land before Item 2 for
>   gauge-map nav synergy" rationale evaporates — Item 5 stands
>   on its own; ordering becomes flexible. (C) Decisions §2
>   updated: gauges visible by default with a checkbox toggle;
>   zoom-graded sizes shipped together. (D) URL hash gains an
>   optional `gauges=off` segment when the toggle is off; default
>   ON state writes nothing (shorter URLs in the common case).
>   (E) `addFilterControl()` at `map.js:307` gains a third
>   `<fieldset>` ("Layers") with a single "Show gauges" checkbox.
>   (F) The existing `map.on('zoomend', …)` handler at
>   `map.js:240`-ish already exists for line-weight
>   recomputation — extend it to also restyle gauge markers at
>   the zoom threshold. No new zoom listener needed.
> - iter 12 (2026-05-15, stopping): 4 findings on the merger.
>   (A) `_build_map_page()` signature gains two parameters
>   (`gauges_geom_url`, `gauges_state_url`). The `<div id="map">`
>   carries two new data attributes — same pattern as the
>   existing reach URLs. (B) **fitBounds question.** First-paint
>   `fitBounds` at `map.js:280` uses visible reach features. If a
>   gauge sits outside the reach-coverage box (e.g., the new
>   Below Falls gauge I just added on a reach we don't have
>   geometry for yet), it'd be off-screen at first paint. Fix:
>   when gauges are visible, fit to the union (reaches +
>   markers). If only reaches are visible (toggle OFF), fit only
>   to reaches. (C) Empty-data case: if `gauges-geom.json` 404s
>   or is empty, the toggle still appears but does nothing.
>   Acceptable; log a console warning. (D) Status colors on
>   markers reuse the existing `COLORS` map at `map.js`-top
>   (already keyed by `'low' | 'okay' | 'high' | 'unknown'`).
>   No new palette constant needed. Stale gauges (>1 d, <7 d)
>   render in the same grey as `unknown` but with a small
>   opacity reduction (e.g., 0.5) to distinguish — borrow the
>   `rp-stale` opacity from the reach popup at `map.js:189`.
>   Convergence (iter 1-12): 6 → 4 → 3 → 4 → 5 → 4 → 4 → 3 → 3 →
>   <decisions> → <redesign> → 4. Stopping.
>
> Dates absolute. References `file:line` against current `main`.

## Why

Five UI improvements requested in conversation 2026-05-15
(Item 2 redesigned iter-11 to merge gauges into the existing map
rather than add a second map page):

1. **Reach map** — hover on a trace currently boldens it; also want the
   popup to open on hover (desktop only; mobile keeps tap-to-open).
2. **Gauges on the map** — add gauge markers as a toggleable
   layer on the existing `/map.html` (decided iter-11: merge
   rather than build a second map page). Hover/click popups
   carry current readings and link to `/gauge.php?id=N`; markers
   are zoom-graded (smaller at low zoom) and gated by a "Show
   gauges" checkbox in the existing filter panel. No nav rename,
   no new HTML page.
3. **`custom.php` → `picker.php`** — at e.g. `/custom.php?ids=85,58,217,237,2`
   the "Edit selection" link goes to `/picker.php` with no IDs
   pre-checked. The picker already supports `?ids=…` on load
   (`static/picker.js:365-376` already calls `readIdsFromUrl()`), so
   this is a bug in the link, not the picker.
4. **`gauge.php` Associated Reaches table** — add a "Location" column
   (matching `index.html`'s Location column) and drop the "Watershed"
   column, so multi-reach gauges (e.g. gauge 14 / Three Lynx) are
   easier to skim.
5. **Scroll-indicator on overflow-scrolling nav strips** — the
   horizontal nav bars (main nav + letter nav) scroll on narrow
   viewports. Today's CSS has a static right-edge fade that doesn't
   react to scroll position. Make it scroll-position-aware (fade
   shows only when content extends offscreen in that direction).

## Scope inventory (verified against current `main`)

**JS / static assets**

- `static/map.js` (365 lines) — Leaflet 1.9.4 reach map. Hover bolden
  at `:224-233`; popup `buildPopup()` at `:178-201` bound at `:223`.
- `static/picker.js` — already reads `?ids=…` via `readIdsFromUrl()` at
  `:365-376`.
- `static/` is the production JS dir served directly by nginx;
  `src/kayak/web/static/` only contains the three files inlined into
  built HTML (`filters.js`, `levels.js`, `style.css`).

**Python builders**

- `src/kayak/web/build/geojson.py` — `_build_reaches_static()` at
  `:60-89` produces `reaches-geom.json`; `_build_reaches_state()` at
  `:99-149` produces `reaches-state.json`. Both write under
  `public_html/static/`. No gauge analog exists.
- `src/kayak/web/build/shell.py:117-158` — `_build_nav()` for built
  static pages (map.html); `:326-396` — `_build_map_page()` writes the
  whole map.html file via `_atomic_write` (deploy.py:199). Item 2
  extends `_build_map_page()`'s signature to accept gauge JSON
  URLs, emitted on the same `<div id="map">` data attributes as the
  reach URLs.
- `src/kayak/web/build/gauges.py` — builds `gauges.html` (table-of-
  gauges page) and exposes `_gauge_status_from_reaches()` — the
  status helper Item 2's `gauges-state.json` reuses for parity.
- `src/kayak/web/build/levels.py:97` — defines `gauge_location =
  reach.description or (reach.gauge.location if reach.gauge else "") or ""`
  for index.html's Location column.

**PHP**

- `php/includes/header.php:52-106` — `render_nav()` for PHP pages.
  Picker entries use `'Reach<br>Picker'` / `'Gauge<br>Picker'`
  (lines 74-79). Map link at `:87`. Active key `'map'`.
- `php/includes/custom_handler.php:346` — "Edit selection" link
  rendered as `<a href="/picker.php">Edit selection</a>` (no `ids=`).
- `php/picker.php` — server-renders shell + filter pills; data is
  AJAX-fetched at `:18-102`; `picker.js` reads `?ids=…` and pre-checks
  reaches after the fetch completes.
- `php/includes/gauge_detail.php:558-580` —
  `_render_associated_reaches()`. SELECT at `:163` already loads
  `r.description`. Header cols (line 566): Name / River / Class /
  Length / Watershed / Status. Row template at `:577` emits `$rname`
  / `$river` / `$classes` / `$len` / `$basin` / `$status_html`.

**CSS**

- `src/kayak/web/static/style.css:48-77` — current nav scroll
  rules including the unconditional right-edge `mask-image` fade
  at `:70-71` (touched by Item 5).

**Constraints to remember**

- nginx CSP blocks inline scripts/handlers → all JS in external files.
- PHP-FPM lacks `mbstring` → use `strlen`/`substr`, not `mb_*`.
- PHPStan level 8 enforced on PHP changes (`composer analyse`).
- `levels build` is idempotent and stages to `.staging` then per-file
  rename (project memory `project_public_html_standalone`).

## Item 1 — Reach map hover-to-popup (desktop only)

**Current behavior.** Hover boldens the trace (`HOVER_LINE` style at
`static/map.js:138`, applied via mouseover handler at `:224-233`).
Popup bound at `:223` is click-triggered.

**Proposed change.** On desktop only, hover also opens the popup;
mouseout closes it. Mobile still uses tap-to-open.

**Desktop detection.** `window.matchMedia('(hover: hover) and (pointer: fine)').matches`.
Evaluated once at module load; cached as `const DESKTOP_HOVER = …`.

**Mechanic — two-surface hover tracking.** Naive
"mouseout-closes-popup" breaks the existing click-the-popup flow:
the reach popup is wrapped in `<a href="/description.php?id=…">`
(see `buildPopup` at `:178-201`), so the user must be able to move
cursor from trace into popup to click. Solution:

- Open the popup in the existing mouseover handler at `:224-228`
  (after the style change), gated on `DESKTOP_HOVER`:
  `target.openPopup()` (no `latlng` arg — let Leaflet anchor to the
  bound popup's natural position).
- Track hover on BOTH the trace and the popup. Maintain a per-target
  flag `_mfPopupHovered`. The trace mouseout handler at `:229-233`
  schedules a 150 ms close timer instead of closing immediately.
- Use Leaflet's `popupopen` event to attach `mouseenter` (cancel
  timer, set `_mfPopupHovered = true`) and `mouseleave` (set
  `_mfPopupHovered = false`, schedule close) listeners on
  `e.popup.getElement()`. Close only when neither surface is
  hovered, after the grace window.
- Click on either surface still works (Leaflet's click-to-open is
  unaffected; popup-internal `<a>` click navigates).

**Pseudo-code skeleton** (drop into the existing event block at
`:223-233`):

```js
let closeTimer = null;
const scheduleClose = () => {
  clearTimeout(closeTimer);
  closeTimer = setTimeout(() => {
    if (!layer._mfHovered && !layer._mfPopupHovered) target.closePopup();
  }, 150);
};
const cancelClose = () => clearTimeout(closeTimer);

target.bindPopup(buildPopup);
target.on('mouseover', () => {
  layer._mfHovered = true;
  layer.setStyle(HOVER_LINE);
  if (layer._mfCasing) layer._mfCasing.setStyle(HOVER_CASING);
  cancelClose();
  if (DESKTOP_HOVER) target.openPopup();
});
target.on('mouseout', () => {
  layer._mfHovered = false;
  layer.setStyle(REST_LINE);
  if (layer._mfCasing) layer._mfCasing.setStyle({weight: REST_CASING.weight});
  if (DESKTOP_HOVER) scheduleClose();
});
target.on('popupopen', e => {
  const el = e.popup.getElement();
  if (!el) return;
  el.addEventListener('mouseenter', () => { layer._mfPopupHovered = true; cancelClose(); });
  el.addEventListener('mouseleave', () => { layer._mfPopupHovered = false; scheduleClose(); });
});
```

**Edge case — popup intercepts cursor.** With the 150 ms grace
window, normal cursor traversal from trace edge to popup interior
falls well inside the window. Default Leaflet popup offset (above
the feature) places the popup clear of the cursor on most
geometries; only very long horizontal traces near the top of the
viewport might force a downward popup that overlaps the cursor.
Test with a few reach geometries; bump grace to 200 ms if needed.

**Touch + hover devices** (e.g., iPad with stylus): `(hover: hover)`
returns true. "Hover" maps to a tap-and-hold gesture, which would
fire mouseover and open the popup — equivalent to today's tap-to-
open behavior. Acceptable.

**Files affected**

- `static/map.js` — ~25 lines added (closeTimer + scheduleClose/
  cancelClose helpers, popupopen event handler, openPopup call in
  mouseover, gating on `DESKTOP_HOVER`).

**Risk.** Low. Pure JS; reach map is already battle-tested. Worst
case the grace timer needs tuning by ±50 ms.

## Item 2 — Add gauge layer to the existing reach map

Iter-11 redesign: gauges merge into `/map.html` as a toggleable
layer rather than getting their own page. No new HTML / no new
JS file / no nav rename / no sitemap entry. Three sub-pieces.

### 2a — JSON data pipeline

Add `_build_gauges_static()` and `_build_gauges_state()` to
`src/kayak/web/build/geojson.py`, paralleling the reach versions at
`:60-149`.

**`gauges-geom.json`** (FeatureCollection of Points, long-cached with
content-hash query string):

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "id": 14,
      "properties": {
        "name": "Clackamas at Three Lynx Creek",
        "river": "Clackamas",
        "location": "Three Lynx Creek",
        "state": "OR",
        "drainage_area": 479.0,
        "elevation": 1120.0
      },
      "geometry": { "type": "Point", "coordinates": [-122.0734, 45.1248] }
    }
  ]
}
```

**`gauges-state.json`** (object keyed by gauge id, short-cached):

```json
{
  "14": {
    "s": "okay",
    "flow": { "v": 2400, "u": "cfs" },
    "gage": { "v": 4.12, "u": "ft" },
    "temperature": { "v": 51.8, "u": "°F" },
    "ts": "2026-05-15T16:00:00Z",
    "stale": false
  }
}
```

**Filtering.** Drop expired gauges (>7 d) — same threshold the
gauges.html page uses (`_gauge_observation_age` in
`src/kayak/web/build/gauges.py`). Include stale (>1 d) with
`"stale": true`. Skip gauges with NULL latitude/longitude (e.g.
some calc gauges); log the skip count.

**Status field — must match gauges.html.** The `s` field in
`gauges-state.json` should be produced by the same
`_gauge_status_from_reaches()` helper that `gauges.py` already
uses, so colors agree between the map and the table. Gauges with
no associated reaches have no derived status — render as `unknown`
(grey).

**Calc gauges.** Include if they carry lat/long; otherwise skip
silently.

### 2b — Build wiring

Hook the new functions into `src/kayak/web/build/deploy.py` next to
the existing reach JSON writes (`:183-187`). Append the hash to the
geom URL the same way reach JSON does. Pass both new URLs into
`_build_map_page()` (alongside the existing `geom_url` / `state_url`
parameters) so the built HTML can carry them on the `<div id="map">`
data attributes for `map.js` to read.

### 2c — Extend `static/map.js` + filter panel

Add a gauge layer on top of the existing reach rendering. All
new logic lives in `static/map.js`; no new JS file.

**Data fetch.** After the existing reach JSON fetches at
`:113-122`, fetch `gauges-geom.json` and `gauges-state.json` from
the data attributes set by 2b. Treat fetch failure as
non-fatal — the rest of the map still renders.

**Rendering.** Build a separate `L.layerGroup` for gauges so it
can be added/removed wholesale by the toggle. For each gauge:

- Visible `L.circleMarker` (radius 3 px when zoom < 9, 7 px at
  zoom ≥ 9 — see "Zoom-graded sizes" below). Fill = status color
  from the existing `COLORS` map at the top of `map.js`
  (keyed by `low | okay | high | unknown` — no new palette
  constant). Stale gauges (>1 d, ≤7 d) render in the `unknown`
  grey at reduced opacity (~0.5), mirroring `rp-stale` opacity
  on the reach popup at `:189`. Stroke `#333` 1 px for
  legibility against tile backgrounds.
- Transparent `L.circleMarker` of radius 14 px on top as the
  mobile tap target, same pattern as the point-reach
  `HIT_POINT` at `:218`. Both bind the same popup.
- Popup wrapped in `<a href="/gauge.php?id=<id>">…</a>` so
  hover-to-click flow matches Item 1's mechanic; reuse Item 1's
  two-surface hover tracking. Popup content: gauge display
  name, location, primary reading (flow > inflow > gage), age
  string, status badge.

**Z-order.** Markers render above reach lines (Leaflet renders
later-added layers on top). The existing `refilter()` adds
lines via three passes (casings → lines → hits); the gauge
layerGroup adds last. Result: marker over trace at gauge
locations — visible and clickable.

**fitBounds on first paint.** The existing first-paint
`fitBounds` at `map.js:280` uses visible reach features only.
Extend it: when the gauge toggle is ON at first paint, fit to
the union of visible reach features AND gauge markers. When OFF,
fit to reaches only (current behavior). Otherwise a gauge sitting
outside the reach-coverage box (e.g., a standalone river-mouth
gauge with no traced reach) would be off-screen at load.

**Layer toggle ("Show gauges").** Extend
`addFilterControl(sSet, cSet, onChange)` at `:307` to take a
fourth argument `gauges` (boolean) and emit a third `<fieldset>`
labelled "Layers" with one checkbox "Show gauges". On change,
add/remove the gauge `L.layerGroup` from the map (cheap O(1)
operation; no per-feature toggling). Default state: ON.
Persists in the URL hash as `gauges=off` only when off (default
ON writes nothing — keeps URLs short in the common case).

**Zoom-graded sizes (mitigation C).** Restyle markers in the
existing `map.on('zoomend', …)` handler at `:240`-ish. Constants
near the top of the file:

```js
const GAUGE_ZOOM_THRESHOLD = 9;
const GAUGE_RADIUS_LOW = 3;
const GAUGE_RADIUS_HIGH = 7;
const GAUGE_HIT_RADIUS = 14; // hit shape unchanged across zooms
```

In the zoomend handler, iterate the gauge markers and set radius
based on `map.getZoom() < GAUGE_ZOOM_THRESHOLD`. Initial render
uses the current map zoom (already known when markers are
created). Hit shape stays at 14 px regardless of zoom — tap
target doesn't shrink at state-wide views.

**Hover tracking + popups.** Apply Item 1's
mouseover/mouseout/popupopen handlers to the gauge marker hit
shape. Same `DESKTOP_HOVER` constant, same 150 ms grace timer,
same closeTimer flag stored on the layer.

**CSS reuse.** The popup uses the existing `.reach-popup`
selector family from `style.css:418` (and the inline `<style>`
block in `_build_map_page`). One additional rule for
`.gauge-popup` (or extend `.reach-popup` to be styling-shared)
adds gauge-specific bits if needed — TBD during implementation.

### Item 2 v1 scope vs deferred

**Settled for v1** (see Decisions §1-2):

- Marker size: zoom-graded 3 px (zoom < 9) / 7 px (zoom ≥ 9)
  visible, plus 14 px transparent hit shape (constant).
- Layer control: single "Show gauges" checkbox in the filter
  panel; default ON. The reach Status / Class filters do NOT
  apply to gauges — they only affect reach lines.

**Deferred** (out of v1 scope):

- Drainage-area-scaled marker sizes.
- A separate gauge-only status filter on the gauge layer.
- Marker clustering (Leaflet has plugins; not needed at 150
  markers).
- Inter-page "view as gauges / reaches" toggle (no longer
  applicable — single map).

## Item 3 — `custom.php` "Edit selection" passes ids

**Root cause.** The `<a href="/picker.php">Edit selection</a>` line
inside `_render_custom_header()` (called from
`custom_handler.php:49`) passes no ids. The picker JS at
`static/picker.js:365-376` already extracts `?ids=…`, auto-checks
the relevant state pills, fetches data, and pre-checks the matching
rows — so no JS or picker.php changes are needed.

**Fix.** `_render_custom_header()` does not receive the original
`$ids` array (only `$reaches` and `$tiers_by_reach`). Reconstruct
the id list inline from `$reaches`:

```php
$id_csv = implode(',', array_map(static fn($r) => (int)$r['id'], $reaches));
$href = '/picker.php' . ($id_csv !== '' ? '?ids=' . $id_csv : '');
echo '<a href="' . htmlspecialchars($href, ENT_QUOTES) . '">Edit selection</a>';
```

This naturally drops any ids that the DB could not resolve (since
they aren't in `$reaches`), which is the right behavior — the
picker shouldn't pre-check ghosts. No function-signature changes
needed.

**Files affected**

- `php/includes/custom_handler.php` — inside `_render_custom_header()`
  (the line `<a href="/picker.php">Edit selection</a>` near `:346`).

**Verification.** Visit `/custom.php?ids=85,58,217,237,2`, click
"Edit selection", confirm the picker loads with those five reaches
pre-checked in the "Selected" panel.

## Item 4 — gauge.php Associated Reaches: +Location, -Watershed

**Current state.** `php/includes/gauge_detail.php:558-580`
`_render_associated_reaches()`. Header line `:566` and row line
`:577` carry six cells: Name / River / Class / Length / Watershed /
Status. Watershed is `$r['basin']`. SELECT at `:163` already loads
`r.description`, which is the same field that drives index.html's
Location column.

**Change.** Replace Watershed with Location. Final column order:
**Name / Location / River / Class / Length / Status** (Location
right after Name; see iter-2 (C) — on a per-gauge page, Location
differentiates reaches more strongly than River, which is often the
same value across all rows). Cell content is
`htmlspecialchars($r['description'] ?? '')` — empty string when
unset, no fallback to `gauge.location` (which would render the same
value on every row of this page and defeat the purpose).

**Files affected**

- `php/includes/gauge_detail.php:566` (header), `:577` (row).

**Verification.** Render `/gauge.php?id=14` (Three Lynx, several
associated reaches). Confirm: Location column appears between River
and Class; Watershed column gone; cell text differentiates the
reaches (June Creek section vs. Collawash sections vs. SF Clackamas
etc.).

## Item 5 — Scroll-position indicator on overflow-scrolling nav strips

**Current state.** `style.css:70-71` applies an unconditional
`mask-image: linear-gradient(to right, #000 calc(100% - 14px),
transparent)` to `<header> > nav` and `<header> > .letter-nav`
when viewport `<768px`. The fade is always visible while the
media query matches — even when scrolled all the way to the end.
There is no left-edge fade after scrolling. Above 768px, even if
content happens to overflow, no fade applies.

**Proposed change.** Replace the unconditional mask with
scroll-position-aware behavior. A small JS helper observes each
scrollable element and toggles `data-overflow-left` /
`data-overflow-right` based on `scrollLeft` vs
`scrollWidth − clientWidth`. CSS targets those attributes for the
fade. Works on any viewport — the JS detects actual overflow, the
media-query gate is dropped.

**JS — `static/scroll-indicator.js`** (~30 lines):

```js
(function () {
  'use strict';
  const SLACK = 2;  // px tolerance for "fully scrolled to edge"
  const update = el => {
    const max = el.scrollWidth - el.clientWidth;
    el.toggleAttribute('data-overflow-left', el.scrollLeft > SLACK);
    el.toggleAttribute('data-overflow-right', el.scrollLeft < max - SLACK);
  };
  const ro = new ResizeObserver(entries => entries.forEach(e => update(e.target)));
  document.querySelectorAll('[data-scroll-indicate]').forEach(el => {
    update(el);
    el.addEventListener('scroll', () => update(el), { passive: true });
    ro.observe(el);
  });
})();
```

**CSS — replace `style.css:48-77`** with attribute-conditional
rules:

```css
@media (max-width: 767px) {
  header > h1 { flex-shrink: 0 }
  header > nav:not(.letter-nav) { flex: 1 1 0; min-width: 0 }
  header > .letter-nav { flex-basis: 100%; margin-left: 0 }
  header > nav,
  header > .letter-nav {
    flex-wrap: nowrap;
    overflow-x: auto;
    overflow-y: hidden;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
  }
  header > nav::-webkit-scrollbar,
  header > .letter-nav::-webkit-scrollbar { display: none }
  header > nav > a,
  header > .letter-nav > a { flex-shrink: 0 }
}
/* Indicator — applies on any viewport when content overflows. */
[data-scroll-indicate][data-overflow-right]:not([data-overflow-left]) {
  -webkit-mask-image: linear-gradient(to right, #000 calc(100% - 14px), transparent);
          mask-image: linear-gradient(to right, #000 calc(100% - 14px), transparent);
}
[data-scroll-indicate][data-overflow-left]:not([data-overflow-right]) {
  -webkit-mask-image: linear-gradient(to right, transparent, #000 14px);
          mask-image: linear-gradient(to right, transparent, #000 14px);
}
[data-scroll-indicate][data-overflow-left][data-overflow-right] {
  -webkit-mask-image: linear-gradient(to right, transparent, #000 14px, #000 calc(100% - 14px), transparent);
          mask-image: linear-gradient(to right, transparent, #000 14px, #000 calc(100% - 14px), transparent);
}
```

**Markup change.** Add `data-scroll-indicate` to the two scrollable
nav elements:

- `php/includes/header.php:render_nav()` — main `<nav>` plus
  `.letter-nav` (if rendered there).
- `src/kayak/web/build/shell.py:_build_nav()` (line `:139`-ish) and
  letter-nav emission (line `:202`-ish).

**Script include.** Add `<script src="/static/scroll-indicator.js" defer></script>`:

- `header.php` `<head>` section (one place; affects all PHP pages).
- The shared head fragment used by `_build_map_page()` and other
  Python builders (one place if a head helper exists; otherwise
  inject into each builder).

**Files affected**

- `src/kayak/web/static/style.css:48-77` — replace fixed
  `mask-image` rules with attribute-conditional ones; drop the
  media-query gate from the mask (keep the flex/overflow rules).
- `static/scroll-indicator.js` — new, ~30 lines.
- `src/kayak/web/build/shell.py` — `_build_nav()` adds attribute;
  shared head fragment adds `<script>`.
- `php/includes/header.php` — `render_nav()` adds attribute;
  `<head>` adds `<script>`.

**Edge cases**

- **First-paint flicker.** Before JS runs, attributes are absent
  and the new CSS shows no fade. Today's user sees a right fade
  pre-JS. Iter-8 (C) chose to accept this — script is ~1 KB,
  `defer`-loaded, sub-100 ms delay. Alternative: pre-set both
  attributes server-side so the worst-case state (both fades)
  shows until JS refines it.
- **Keyboard scroll** (Tab-into-nav-link triggers browser auto-
  scroll): scroll event fires, attributes update. ✓
- **Touch scrolling on iOS**: momentum scrolling emits scroll
  events; fade transitions during fling. ✓
- **Generic utility.** `[data-scroll-indicate]` is element-agnostic;
  any future scrollable strip (tab bar, pill row) can opt in by
  adding the attribute. Document this in the JS file header.

**Risk.** Low. Pure progressive enhancement — if JS fails, no
fade applies (worse than today, where a static right fade always
applies, but functionally equivalent: the nav still scrolls).

## Implementation order

1. **Item 4** (PHP edit, ~10 min)
2. **Item 3** (1-line PHP, ~5 min)
3. **Item 1** (JS edit, ~25 min) — proves the hover-popup
   mechanic before Item 2 reuses it on gauge markers.
4. **Item 5** (JS + CSS + attribute, ~30 min) — independent;
   can land any time. Iter-7 D's "before Item 2 for nav synergy"
   rationale is obsolete now that Item 2 has no nav change.
5. **Item 2** (JSON pipeline + map.js extension, ~1.5-2 hrs) —
   reduced from 3-4 hrs by iter-11 merger.

All five items are independent PRs. Item 2 lands as a single PR
covering 2a (JSON), 2b (build wiring), and 2c (map.js + filter
panel) — splitting would produce two un-shippable intermediate
states (JSON without consumer / extended map.js without data).

## Testing approach

- **Item 5**: in-browser at narrow width (320-767 px) on `levels-test`.
  Verify: at scroll position 0, only right fade shows; mid-scroll
  both fades show; end-of-scroll only left fade shows. Resize wide
  enough that the nav fits — both fades disappear. Tab into a nav
  link off the right edge — browser auto-scrolls, fades update.
  Disable JS — verify nav still scrolls (no indicator, acceptable
  graceful degradation).
- **Items 3 + 4 (PHP changes)**: run `composer test` (PHPUnit) and
  `composer analyse` (PHPStan level 8) before merging. Iter-6 noted
  existing tests don't assert "Watershed" or the outbound picker URL
  — but new assertions could be added (e.g., `assertStringContainsString('Location', $body)`
  for Item 4; `assertStringContainsString('?ids=', $body)` for Item 3).
- Item 4: in-browser render of `/gauge.php?id=14` on `levels-test`.
  Verify Location column appears immediately after Name; Watershed
  is gone; rows are differentiated.
- Item 3: in-browser flow `/custom.php?ids=…` → "Edit selection" →
  picker shows checks. Try a 1-id, 5-id, and missing-id case.
- Item 1: in-browser hover on a desktop browser; tap on a mobile
  device (or DevTools mobile emulation with `pointer: coarse`).
  Verify popup opens on hover, stays open as cursor moves into it,
  closes ~150 ms after cursor leaves both surfaces. Click into the
  popup link still navigates to `/description.php?id=…`.
- Item 2: `levels build`, then in-browser `/map.html`. Verify:
  - `gauges-geom.json` + `gauges-state.json` written under
    `public_html/static/` with sensible feature count
    (~150 after expiry filter).
  - Gauge markers appear on top of reach lines at default zoom;
    status colors agree with `gauges.html` table.
  - "Show gauges" checkbox in filter panel toggles the layer on
    and off; toggling off writes `gauges=off` into the URL hash;
    toggling back on removes the segment.
  - Marker hover (desktop) → popup opens; click into popup link
    navigates to `/gauge.php?id=N`. Touch tap on mobile opens
    popup; tap on link navigates.
  - Zoom out to state-wide view: markers shrink to ~3 px dots
    (still visible but unobtrusive). Zoom in past zoom 9:
    markers grow to ~7 px.
  - Hit-target on mobile: at low zoom, 3 px visible marker but
    14 px tap target — small markers are still tappable.
  - No regression: reach lines still hover-bolden, click pop,
    Status / Class filters still work, fitBounds still happens.
  - Nav unchanged: "Map" still labelled "Map"; active state on
    `/map.html` still works.

## Decisions (settled 2026-05-15)

All six v1 decisions accepted as recommended. Each remains tunable
during implementation if a chosen value misbehaves in the browser.

1. **Item 2 marker size** — zoom-graded `circleMarker`: **3 px**
   visible radius when zoom < 9, **7 px** when zoom ≥ 9.
   Transparent hit shape **14 px** at all zooms (constant
   mobile tap target). Drainage-area-scaled sizes deferred.
2. **Item 2 layer control** — a single "Show gauges" checkbox in
   the existing filter panel (`addFilterControl()` at
   `map.js:307`). Default ON. Does NOT participate in the
   existing reach Status / Class filters — gauges are visible
   regardless of which reach statuses are filtered.
3. **Item 4 column order** — **Name / Location / River / Class /
   Length / Status**. Location right after Name; reach.description
   is the strongest differentiator on a per-gauge page (iter 3).
4. **Item 4 empty description** — render a **blank cell** when
   `r.description` is empty. Matches `index.html` Location-column
   behavior (no em-dash, no placeholder).
5. **Item 1 grace delay** — **150 ms** before closing the popup
   when neither the trace nor the popup is hovered. Tuning band
   100-200 ms if it feels wrong in the browser.
6. **Item 5 first-paint** — **accept the brief no-fade gap**
   before JS attaches (`defer`-loaded ~1 KB script, sub-100 ms).
   No server-side `data-overflow-*` fallback. Revisit only if the
   gap is visibly jarring on slow connections.
