# Plan — Phase 3 of PLAN_js_cleanup: `var → const/let` modernization

**Status:** Done. P3a `598c422` (map.js + plot-hover.js), P3b `bf300d7`
(filters.js + feature-map.js), P3c `39e4c95` (reach-map / search-map /
levels), P3d `142c2cb` (`noVar` + `useConst` biome gates promoted to
errors). Closeout recorded in `3a8ab05`.

## Context

`docs/done/PLAN_js_cleanup.md` Phase 1 (lint coverage gap) landed as commit `87226ed`; Phase 2 (strict-mode adoption) landed as `c8363a0`. Both are on `origin/main`. Phase 3 — eliminating the last 236 `var` declarations across 7 hand-written JS files and installing a permanent `noVar`/`useConst` gate in `biome.json` — was left as an explicit decision point. User has chosen to execute it: **full 4-commit Phase 3 with full per-commit browser smoke walks.**

What this accomplishes:
- Every hand-written JS file becomes uniform on `const`/`let` (block-scoped, immutable-by-default). Matches the 3 files (`picker.js`, `gauge_picker.js`, `sw.js`) that are already 100% modern.
- Promotes biome's `noVar` from silent to `"error"` and removes the `useConst: "off"` suppression — any new `var` introduced in a tracked file will fail CI; any reassign-free `let` will auto-flag for `const` upgrade. Permanent gate against re-introducing the old style.
- No functional change to the site; verification gate is "identical behavior" before and after.

## Hot-spot inventory (from iterative audit)

Of the 236 var declarations, only **2 clusters** have a hazard that biome's autofix can't catch. The other 234 are mechanical.

### Hot spot 1 — `static/map.js:276-299` (5-loop var-reuse)

Five `for` loops in the same function share one function-scoped `i`. Only the first declares `var`:

```js
for(var i=0;i<visible.length;i++)if(visible[i]._mfCasing)group.addLayer(visible[i]._mfCasing);
for(i=0;i<visible.length;i++)group.addLayer(visible[i]);
for(i=0;i<visible.length;i++)if(visible[i]._mfHit)group.addLayer(visible[i]._mfHit);
// ... comment block ...
for(i=0;i<visible.length;i++)visible[i].bringToFront();
for(i=0;i<visible.length;i++)if(visible[i]._mfHit)visible[i]._mfHit.bringToFront();
```

Biome's `noVar` autofix would convert line 276 to `for(let i=0;…)`. Lines 277/278/298/299 would then assign to an undeclared `i` — under strict mode (Phase 2), that throws `ReferenceError`. **Page breaks.**

**Fix (hand-applied, before biome runs on this file):** convert all 5 loops to `for(let i=0;…)`. Each loop gets its own block-scoped `i`; preserves the original intent of "use `i` only as the loop counter."

### Hot spot 2 — `static/search-map.js:5-7` (try-block escape)

```js
try{var reaches=JSON.parse(el.dataset.reaches);var colors=JSON.parse(el.dataset.colors)}
catch{return;}
if(!reaches||!colors)return;
```

`reaches` and `colors` are `var`-declared inside the try block but referenced outside (line 7's `if(!reaches||!colors)`). Biome's autofix would block-scope them to the try; line 7 becomes `ReferenceError`. **Page breaks.**

**Fix (hand-applied, before biome runs on this file):** hoist the declarations out of the try, keep assignments inside:

```js
let reaches, colors;
try{reaches=JSON.parse(el.dataset.reaches);colors=JSON.parse(el.dataset.colors)}
catch{return;}
if(!reaches||!colors)return;
```

### Confirmed non-hazards

Iter 3 verified these patterns are safe:

- **9 same-name `var` duplicates** across `map.js`/`plot-hover.js`/`filters.js`/`feature-map.js`/`search-map.js` are all in **different function scopes** (separate Leaflet callbacks, separate inner functions, separate `forEach` callbacks). No same-scope `let`-redeclaration `SyntaxError` risk.
- **`feature-map.js:31-39` and `reach-map.js:19-26`** IIFE pattern `(function(lat,lon){...})(ll[0],ll[1])` is safe — IIFE parameters create fresh per-iteration binding; the synchronously-called `m.on()` binds to this iteration's marker; the inner click handler captures only `lat`/`lon` (not `m` or the loop index).
- **`plot-hover.js:87`** try-catch is safe — `var payload;` is declared outside the try; the try body only assigns.
- No `switch`/`case` declarations, no function declarations inside blocks, no hoisting reliance, no arrow-function `var` captures, no eval/Function constructor use.

## Precondition: run `levels build`

Phase 1+2 surfaced that `public_html/` on this dev box is un-built — missing `levels.js`, `filters.js`, `style.css.hash`, `reaches-geom.json`, `reaches-state.json`, and `map.html` is stale (pre-geom/state split). This blocked Phase 2 smoke-testing of the dynamic PHP surface.

Phase 3 commits 3a/3b/3c need full per-commit smoke walks across the PHP surface. **Run `levels build` once before starting** (or via `! /home/pat/.venv/bin/levels build` after exiting plan mode):

```bash
/home/pat/.venv/bin/levels build
```

Verifies:
- `public_html/static/levels.js` exists
- `public_html/static/filters.js` exists
- `public_html/static/style.css.hash` exists with a matching `style-<hash>.css` sibling
- `public_html/static/reaches-geom.json` and `reaches-state.json` exist
- `public_html/map.html` is regenerated (new content references `/static/map.js`, not unpkg CDN)

If the macOS dev box lacks a working venv + DB, an alternative is to do the smoke testing on the live site staging URL, or accept that 3a/3b/3c can only smoke-test `/map.html` locally (covers `map.js` but not the PHP surface). The recommended path is run `levels build` locally.

## Critical files

| File | Phase | Reason in scope |
|---|---|---|
| `static/map.js` | 3a | 73 vars; **hot spot at L276-299** |
| `static/plot-hover.js` | 3a | 50 vars; mechanical |
| `src/kayak/web/static/filters.js` | 3b | 43 vars; mechanical |
| `static/feature-map.js` | 3b | 31 vars; mechanical |
| `static/reach-map.js` | 3c | 19 vars; mechanical |
| `static/search-map.js` | 3c | 13 vars; **hot spot at L5-7** |
| `src/kayak/web/static/levels.js` | 3c | 7 vars; mechanical |
| `biome.json` | 3d | rule flip — enable `noVar: "error"`, remove `useConst: "off"` |

Out of scope (unchanged): `static/picker.js`, `static/gauge_picker.js`, `static/sw.js` (0 vars each; already modern), `static/leaflet.js` (vendored, intentionally excluded), `docs/map-color-tune/map3.js` (not deployed).

## Reuse

- **Biome's `noVar` rule** — already in biome's `suspicious` category (not currently in `biome.json`); enable temporarily during conversion. Autofix is `unsafe`-categorized (`biome check --write --unsafe`).
- **Biome's `useConst` rule** — currently `"off"` in `biome.json` `rules.style`; remove that line in 3d to enable the recommended default `"error"`. Autofix is `safe` (`biome check --write`).
- **Existing biome.json suppression of `noRedundantUseStrict`** (added in Phase 1, commit `87226ed`) stays in place — required for the strict-mode directives Phase 2 added.
- **No new tests, no new fixtures.** Phase 3 reuses Phase 2's verification gate plus per-commit browser smoke walks.

## Per-commit workflow

Three semantically-identical commits (3a/3b/3c) followed by one config-only commit (3d):

### Commit 3a — `map.js` + `plot-hover.js` (123 vars)

1. **Pre-fix the hot spot.** Hand-edit `static/map.js:276-299` to convert all 5 `for` loops to `for(let i=0;…)`. No biome involvement; pure manual edit.
2. **Enable biome rules temporarily (local working state, don't commit).** Edit `biome.json` `rules.suspicious` to add `"noVar": "warn"`; edit `rules.style` to change `"useConst": "off"` → `"useConst": "warn"`.
3. **Run autofix:** `biome check --write --unsafe static/map.js static/plot-hover.js` (applies `noVar` → `let`). Then `biome check --write static/map.js static/plot-hover.js` (applies `useConst` → `const` where safe).
4. **Revert the biome.json temp changes** (the rule flip happens in 3d, not here).
5. **Verify zero vars remain:** `grep -cE '^\s*var ' static/map.js static/plot-hover.js` should print `0` for both.
6. **Diff review:** `git diff static/map.js static/plot-hover.js`. Spot-check that no `let` declarations are inside `if`/`for`/`try` blocks while referenced outside (biome won't always catch this — iter 3's audit said no other such patterns exist in these files, re-verify on the actual diff).
7. **Smoke test (full per-commit walk):**
   - `http://localhost:8000/map.html` — pan, zoom, layer-control toggle (Topo/Street/Satellite), filter checkboxes (every status + every class tier), click a reach polyline to open its popup, click "Reset" if present, refilter to "none selected" and back. Watch console for `ReferenceError`/`TypeError`.
   - `http://localhost:8000/description.php?id=<N>` — hover the SVG sparkline at multiple X positions; verify popup tracks correctly.
   - `http://localhost:8000/gauge.php?id=<N>` — same hover test on sparklines.
   - `http://localhost:8000/reach.php?id=<N>` — same.
   - `http://localhost:8000/picker.php` — same (sparklines in result rows).
   - Side-by-side comparison: open a second tab on `c8363a0` (pre-3a) and visually diff. Should be pixel-identical and behave identically.
8. **Commit 3a** with body listing files, var-count delta, and a one-line note on the map.js hand-fix.

### Commit 3b — `filters.js` + `feature-map.js` (74 vars)

Same shape as 3a, **no pre-fix needed** (both files audited mechanical).

1. Enable biome rules temporarily.
2. `biome check --write --unsafe src/kayak/web/static/filters.js static/feature-map.js`.
3. `biome check --write src/kayak/web/static/filters.js static/feature-map.js`.
4. Revert biome.json temp changes.
5. Verify zero vars remain.
6. Diff review.
7. Smoke test:
   - `http://localhost:8000/` — toggle every filter group (state, basin/HUC, status, class tier); collapse/expand on mobile-emulator-narrow viewport; verify URL hash updates and reload-with-hash restores filters.
   - `http://localhost:8000/picker.php` — exercise filter bar.
   - `http://localhost:8000/gauge_picker.php` — same.
   - `http://localhost:8000/custom.php` — same.
   - `http://localhost:8000/custom_gauges.php` — same.
   - `http://localhost:8000/description.php?id=<N>` — interact with the feature-map (click markers, verify popup contents).
   - `http://localhost:8000/gauge.php?id=<N>` — same.
8. Commit 3b.

### Commit 3c — `reach-map.js` + `search-map.js` + `levels.js` (39 vars)

1. **Pre-fix the hot spot.** Hand-edit `static/search-map.js:5-7` to hoist `let reaches, colors;` before the try block. Keep assignments inside the try.
2. Enable biome rules temporarily.
3. `biome check --write --unsafe static/reach-map.js static/search-map.js src/kayak/web/static/levels.js`.
4. `biome check --write static/reach-map.js static/search-map.js src/kayak/web/static/levels.js`.
5. Revert biome.json temp changes.
6. Verify zero vars remain.
7. Diff review — spot-check `levels.js` since its `'use strict'` is at script top (not in IIFE); verify const/let inference holds across the 5 top-level constructs.
8. Smoke test:
   - `http://localhost:8000/reach.php?id=<N>` — exercise reach-map (track polyline rendering, put-in/take-out marker click → Google Maps); exercise search-map (multi-reach view).
   - `http://localhost:8000/` — verify table-row click navigation (the bug Phase 1+2 smoke testing surfaced as pre-existing — should be fixed by `levels build` populating `/static/levels.js`); verify time-string conversion from UTC; verify sticky-header height tracking on scroll.
   - `http://localhost:8000/<any-state>.html` — same row-click + time checks on state pages.
9. Commit 3c.

### Commit 3d — rule flip

1. Edit `biome.json`:
   - In `rules.suspicious`: add `"noVar": "error"`.
   - In `rules.style`: **remove** the `"useConst": "off"` line entirely so biome's recommended default (`"useConst": "error"`) takes effect.
2. Run `biome check`. Expected: clean (no remaining `var`s in tracked files; no const-able `let`s left).
3. If biome surfaces any `useConst` upgrade misses from 3a/b/c: run `biome check --write` to apply, then stage those JS changes alongside the biome.json change in the same 3d commit.
4. Run `make lint-js` — also clean.
5. Commit 3d.

## Verification

End-to-end verification at the end of Phase 3 (after 3d):

```bash
# 1. No vars remain in tracked JS
grep -rE '^\s*var ' static/*.js src/kayak/web/static/*.js | grep -v leaflet.js
# Expected: empty.

# 2. Biome enforces it going forward
biome check
# Expected: "Checked 12 files. No issues found."

# 3. Make lint-js (the CI-equivalent local check)
make lint-js
# Expected: clean.

# 4. Regression test the rule: try to add a var, biome rejects
echo 'var x = 1;' >> static/sw.js
biome check static/sw.js  # Expected: lint/suspicious/noVar error
git checkout static/sw.js  # Revert the test edit.

# 5. Full browser smoke walk one more time on every URL exercised in 3a/3b/3c.
```

CI gate after `git push`: the existing `.github/workflows/ci.yml` runs `biome check` (`.github/workflows/ci.yml:53` per the source plan). Any future PR introducing a `var` to a tracked file will fail CI; any non-reassigned `let` will auto-flag for `const` upgrade.

## Decisions baked in (from iterative audit)

- **Pre-fix the 2 hot spots manually before biome runs on those files** — biome's `noVar` autofix would produce broken code on `map.js:276-299` and `search-map.js:5-7`. The fix is small (5 line edits in map.js, 2 line edits in search-map.js) and prevents the autofix from being destructive in those locations.
- **Apply `noVar` and `useConst` in sequence per commit** — `--unsafe` for `noVar`, plain `--write` for `useConst`. Two passes per commit; biome.json rules enabled temporarily as working state only.
- **Commit 3d is the ONLY commit that changes `biome.json`** — the rule flip is the durable gate; 3a/3b/3c each revert any temp biome.json edits before committing. Each of 3a/3b/3c is independently revert-clean.
- **Full per-commit smoke walks** — chosen over lint-only-then-final-smoke. Reason: a `var → let` regression in 3a only surfaces under user interaction; deferring smoke until 3c means a bisect can't narrow blame to a single commit.
- **`levels build` is a precondition** — not a Phase 3 step. Run it once at the top of the session.

## Risks

| Risk | Mitigation |
|---|---|
| `map.js:276-299` hand-fix introduces a typo | Diff review; smoke test `/map.html`'s filter cycle (each loop fires on every filter change) |
| `search-map.js:5-7` hoist regresses error handling | Smoke test: visit `/reach.php?id=<N>` with valid id (success path); inspect with a broken `data-reaches` attribute via DevTools to confirm catch-and-return still works |
| Biome surfaces an unforeseen pattern during autofix and produces broken code | Per-commit smoke walk catches it; each commit is independently revertable (3a→3c don't touch each other's files); biome.json untouched until 3d |
| `useConst` upgrades a `let` that's secretly reassigned by a path biome can't see | Audit found no such cases; smoke test would surface as `TypeError: Assignment to constant variable` |
| `levels build` fails on macOS dev box (no working venv/DB) | Fallback: smoke-test on staging URL or accept partial coverage (only `/map.html` locally); not blocking |

## Out of scope (for Phase 3)

- `static/leaflet.js` — vendored minified library; biome's `includes` doesn't touch it.
- `docs/map-color-tune/map3.js` — developer tool inside `docs/`, not deployed.
- `php/style.css` stale ref in `biome.json` + `Makefile`; `hardening/*.sh` stale glob in `Makefile lint-shell`. Both pre-exist, both deferred to a separate small commit per user direction.
- Phase 4 (if there were one): no current scope. The plan-doc's "End state" table is satisfied by completion of 3a-3d.

## Estimated effort

| Commit | Effort | Bottleneck |
|---|---|---|
| 3a | 1–2 hr | Browser smoke walk of `/map.html` + 4 sparkline-bearing PHP URLs |
| 3b | 30–60 min | Filter-bar smoke walk across 5 URLs |
| 3c | 30–45 min | Reach-page + state-page smoke walks |
| 3d | 5 min | Pure config flip |

Total: ~3–4 hours focused.
