# Plan — JS cleanup (close lint gap + decide on `var` modernization)

> **Cross-check:** plan drafted 2026-05-11 against `main` at `629e97c`. A second Claude session should re-run §Reproduce to confirm the unlinted-files list and the biome warning count before Phase 1 starts.
>
> Dates are absolute. References are `file:line` against current `main`.

## Context

`biome` was wired into CI in commit `ae36d45` ("Add linting for PHP, JavaScript, CSS, and shell scripts") with an explicit `includes` allow-list in `biome.json`. Four JS files — totaling 928 LOC, including `filters.js` (the second-biggest JS file in the repo) — were never added to that list. CI's `biome check` doesn't see them; new bugs in those files don't trip the lint gate.

Two related issues compound the lint gap and need fixing in the same commit:

- **Stale `no_show_review.js` reference.** `public_html/no_show_review.js` was deleted in commit `b91c730` ("cleanup: remove no_show_review artifact") but the path still appears in both `biome.json`'s `includes` and the `Makefile lint-js` target. Biome's response is to emit an `internalError/io` diagnostic but continue with exit 0 — so CI doesn't fail, but the log is noisy and the includes list documents a file that doesn't exist.
- **Makefile `lint-js` paths don't traverse `src/kayak/web/static/`.** The target is `biome check static/ public_html/no_show_review.js src/kayak/web/static/levels.js` — only one specific file in the build-pipeline static dir. Adding `filters.js` to `biome.json`'s `includes` alone has no effect: biome filters traversal candidates through `includes`, but if the CLI never gives it `filters.js` to consider, the include is moot. Both `biome.json` and the Makefile must be updated together.

This plan closes the lint coverage gap (Phase 1, required), harmonizes the project on strict mode (Phase 2, required), and decides on a `var → const/let` modernization pass (Phase 3, optional, scoped separately for clean rollback).

## Why

Biome's lint gate only works for files it lints. Four of the ten hand-written JS files (`gauge_picker.js`, `feature-map.js`, `plot-hover.js`, `filters.js`) are live — referenced from PHP and the build pipeline — but invisible to CI. The exclusion looks accidental: `picker.js` is in the includes but its sibling `gauge_picker.js` is not; `levels.js` is in but its sibling `filters.js` is not.

Strict mode is split unevenly too: only `plot-hover.js` uses `'use strict'`; the other 9 hand-written files run in non-strict mode (classic-script loading + no directive). Implicit-global typos go silent today; strict mode would surface them as `ReferenceError`. Cheap safety win once Phase 1's lint suppression unblocks adding the directive everywhere.

Past that, the codebase is split between modern JS (`picker.js`, `gauge_picker.js`, `sw.js`: 0 `var`s total) and pre-`let`/`const` JS (7 files with 236 `var`s total — biggest offenders: `map.js` 73, `plot-hover.js` 50, `filters.js` 43, `feature-map.js` 31). Whether to harmonize is a lower-stakes call deferred to Phase 3.

Goal: every hand-written JS file is under CI lint with `biome check` reporting 0 warnings; every hand-written JS file runs in strict mode; a documented decision on `var` modernization.

## Current state (verified)

| File | LOC | In biome includes? | `var` count | Referenced from |
|---|---|---|---|---|
| `static/sw.js` | 44 | ✓ | 0 | service worker (auto-registered) |
| `static/map.js` | 363 | ✓ | 73 | embedded in build-time HTML |
| `static/picker.js` | 380 | ✓ | 0 | `php/picker.php` |
| `static/reach-map.js` | 44 | ✓ | 19 | `php/description.php` |
| `static/search-map.js` | 42 | ✓ | 13 | `php/picker.php` |
| `public_html/no_show_review.js` | — | ✓ *(stale)* | — | **file deleted in `b91c730` — entry should be removed** |
| `src/kayak/web/static/levels.js` | 51 | ✓ | 7 | content-hashed by `build/deploy.py` |
| **`static/feature-map.js`** | 109 | ✗ | 31 | `php/description.php`, `php/gauge.php`, `php/includes/gauge_map.php` |
| **`static/gauge_picker.js`** | 343 | ✗ | 0 | `php/gauge_picker.php` |
| **`static/plot-hover.js`** | 211 | ✗ | 50 | `php/includes/footer.php` (every page) |
| **`src/kayak/web/static/filters.js`** | 265 | ✗ | 43 | content-hashed; used by 4 PHP pages + `build/deploy.py` |
| `static/leaflet.js` | 144 KB minified | ✗ | — | vendored library — intentionally excluded (one minified line; biome would dump thousands of warnings) |

Biome run with the four missing files included surfaces **7 warnings**:

```
src/kayak/web/static/filters.js:202:15  lint/complexity/useOptionalChain      Unsafe fix
src/kayak/web/static/filters.js:204:17  lint/complexity/useOptionalChain      Unsafe fix
src/kayak/web/static/filters.js:216:17  lint/complexity/useOptionalChain      Unsafe fix
src/kayak/web/static/filters.js:241:9   lint/complexity/useOptionalChain      Unsafe fix
static/plot-hover.js:30:3               lint/suspicious/noRedundantUseStrict  Safe fix      [misfires — see below]
static/plot-hover.js:88:12              lint/correctness/noUnusedVariables    Unsafe fix
static/plot-hover.js:89:9               lint/complexity/useOptionalChain      Unsafe fix
```

**Only 1 of 7 is a "safe" autofix per biome — and it misfires.** Biome's `noRedundantUseStrict` assumes module semantics (modules are strict-by-default), but every JS file in this project is loaded as a classic `<script src="…" defer>` — verified across `php/`, `src/kayak/web/build/shell.py`, and `src/kayak/web/build/_shared.py`; none use `type="module"`. In a classic script, `'use strict'` inside the `(function(){…})()` IIFE *is* meaningful, and removing it weakens plot-hover.js's safety (re-enables implicit globals on undeclared assignment, returns `this` to the global object).

Note also: `plot-hover.js` is the *only* JS file in the project that uses `'use strict'`. The other 9 hand-written files lack it — they're non-strict by default. The autofix would remove the inconsistency by deleting plot-hover.js's directive, but Phase 2 fixes the inconsistency in the opposite direction (add the directive everywhere). Phase 1 step 3 suppresses `noRedundantUseStrict` to unblock that path; see Decision 1.

The other 6 autofixes (5× `useOptionalChain`, 1× `noUnusedVariables`) require `biome check --write --unsafe`. All five `useOptionalChain` sites are `payload && payload.something` or `opts && opts.something` patterns where the left operand is a JSON-parsed object or a function argument — the new optional-chain form is semantically equivalent (and at `filters.js:241` slightly safer, guarding `matchMedia(X)` returning null). `noUnusedVariables` renames the unused `catch (e)` binding to `_e`. None are in the files that have 0 `var`s — `gauge_picker.js` (343 LOC, biggest of the four unlinted) passes clean immediately.

## Phase 1 — Close the lint coverage gap (1 commit, required)

Three coupled edits + autofix application:

1. **`biome.json` — `files.includes`:**
   - **Add** four entries:
     - `static/feature-map.js`
     - `static/gauge_picker.js`
     - `static/plot-hover.js`
     - `src/kayak/web/static/filters.js`
   - **Remove** the stale `public_html/no_show_review.js` entry (file deleted in `b91c730`).
2. **`Makefile` — `lint-js` target:**
   - Change `src/kayak/web/static/levels.js` → `src/kayak/web/static/` (directory, so biome's traversal picks up `filters.js`).
   - Remove `public_html/no_show_review.js` from the path list (same stale-file reason).
   - Result: `biome check static/ src/kayak/web/static/`.
3. **Suppress `noRedundantUseStrict` before applying autofixes.** Add `"noRedundantUseStrict": "off"` to `biome.json`'s `"rules.suspicious"` block (alongside the existing `noGlobalIsNan: "off"`). `biome.json` doesn't accept JSON comments (verified — biome crashes with an unexpected-error when comments are present), so the rationale lives in this plan + the commit message rather than inline. Reference: classic-script-loaded JS (see Decision 1).
4. **Apply the remaining 6 autofixes:** `biome check --write --unsafe`. Inspect each diff hunk before staging — the 5 `useOptionalChain` rewrites and the 1 `noUnusedVariables` rename are mechanical but biome classifies them "unsafe" for a reason (semantic-shift caveat under §Risks).
5. **Verify clean:** `biome check` (no `--write`) — expect "Checked 12 files in N ms. No issues found." (12 = 10 JS + 2 CSS, all from `biome.json`'s `includes`: 5 previously-linted JS + 4 newly-linted JS + `levels.js` + `filters.js` + `php/style.css` + `src/kayak/web/static/style.css`; `no_show_review.js` no longer counted; the suppressed rule produces no diagnostics; `plot-hover.js`'s `'use strict'` directive stays in place). Pre-Phase-1 baseline: `Checked 8 files` (9 includes minus the missing `no_show_review.js`).

**Verification gate:**
- `biome check` clean (the canonical CI check; same command CI runs at `.github/workflows/ci.yml:53`).
- `make lint-js` clean (mirrors what CI runs after this plan lands).
- `make lint-all` clean (runs all lint targets — `lint lint-php lint-js lint-css lint-shell` — sanity check that nothing else regressed).
- Smoke-test in browser: `php -S localhost:8000 -t public_html`, then:
  - Visit a description page (`description.php?id=1` or similar) — hover the SVG sparkline (exercises `plot-hover.js`).
  - Open the index page, toggle the state filter (`filters.js`).
  - Visit a gauge page (`gauge.php?id=1`) — interact with the map (`feature-map.js`).
  - Open the gauge picker (`gauge_picker.php`) — the no-fix file should still work unchanged.
  - Confirm zero console errors throughout.

## Phase 2 — Strict mode harmonization (1 commit, required)

Only `plot-hover.js` uses `'use strict'`; the other 9 hand-written JS files run in non-strict mode because they're loaded as classic scripts (`<script src="…" defer>`, no `type="module"`) and lack the directive. Service workers registered without `{ type: 'module' }` (the project's case — `levels.js:50` registers `sw.js` with no options) are also non-strict by default. Phase 2 adds `'use strict'` to the 9 missing files so every hand-written JS unit runs in strict mode.

### Scope

9 single-line insertions. 8 go inside an existing `(function(){…})()` IIFE as the first statement; one (`sw.js`) goes at the top of the file (no IIFE — service worker top-level).

| File | Insertion point |
|---|---|
| `static/map.js` | inside the IIFE that starts at line 10 |
| `static/picker.js` | inside the IIFE at line 1 |
| `static/reach-map.js` | inside the IIFE at line 1 |
| `static/search-map.js` | inside the IIFE at line 1 |
| `static/feature-map.js` | inside the IIFE at line 11 |
| `static/gauge_picker.js` | inside the IIFE at line 9 |
| `static/sw.js` | top of file (no IIFE; worker top-level) |
| `src/kayak/web/static/levels.js` | inside the IIFE at line 2 |
| `src/kayak/web/static/filters.js` | inside the IIFE at line 30 |

`plot-hover.js` already has the directive — unchanged.

### Why a separate commit (and why required)

Three reasons strict mode is its own commit, not bundled with Phase 1 or Phase 3:

- **Different risk profile from Phase 1.** Phase 1's autofixes are syntactically mechanical. Strict-mode adoption changes runtime semantics — implicit globals throw `ReferenceError`, `this`-in-bare-function-calls becomes `undefined` instead of `window`, octal literals are rejected. Each behavioral shift is rare in this codebase but possible. Isolating these in their own commit makes `git bisect` land precisely if a regression surfaces.
- **Different risk profile from Phase 3.** Phase 3 (var modernization) changes block scope — also semantic, but a different kind. Bundling strict-mode with var modernization would mean two distinct semantic shifts in the same diff; if a regression appeared on a `git bisect`, you'd have to manually disentangle which change caused it.
- **Required, not optional.** Even if Phase 3 is deferred indefinitely, Phase 2's safety win — failing loud on implicit globals — is worth landing on its own. `noRedundantUseStrict` is already suppressed in Phase 1, so adoption is friction-free from a lint perspective.

### Risk

The 9 files use `var` and IIFEs throughout. Strict mode would surface latent bugs as runtime errors (not silent corruption):

- **Implicit globals.** `foo = bar` in a nested function — without preceding `var`/`let`/`const` — currently creates `window.foo`. Strict mode throws `ReferenceError`.
- **`this` defaults.** A bare `function inner() { this.x }` would read `window.x` in non-strict; strict-mode sees `this === undefined` and throws on property access. Common in callback-heavy code that forgot `.bind(this)`.
- **Octal literals.** `0123` was a valid octal literal in non-strict; strict mode rejects it at parse time with `SyntaxError`.
- **Duplicate parameter names.** `function f(a, a)` — rejected at parse time.

All four failure modes are *parse-time or first-invocation* errors — they fail loud the moment the file is loaded or the function runs. The browser-smoke-test gate is sufficient to catch them; static grep heuristics (e.g. `^\s*[a-zA-Z_]+\s*=` for implicit globals) produce too many false positives against legitimate reassignment patterns to be load-bearing. The files in scope are small enough (largest is `map.js` at 363 LOC) that a one-pass code review pairs well with the smoke-test.

### Verification gate

- **`biome check` clean** — `noRedundantUseStrict` is suppressed from Phase 1, so the 9 newly-added directives (plus `plot-hover.js`'s pre-existing one — 10 total post-commit) generate no warnings.
- **Full browser walk-through** with the JS console open: every page that loads any of these 9 files. Use the same flow checklist as Phase 1's smoke-test plus a gauge-picker page (`gauge_picker.php`) and the index (`/index.html` — loads `levels.js`, `filters.js`, `plot-hover.js`). Any `ReferenceError` / `TypeError` / `SyntaxError` in the console blocks the commit.
- **Service worker check** — `sw.js`'s strict-mode adoption is invisible from page UI; verify by opening Chrome DevTools → Application → Service Workers and confirming the worker registers cleanly (no parse errors in its dedicated log).

## Phase 3 — `var → const/let` modernization (4 commits, optional)

**Decision deferred — see *Decisions to make* below.** If you decide to do it:

Scope is **236 `var` declarations across 7 files**:

| File | `var` count | Phase 3 commit |
|---|---|---|
| `static/map.js` | 73 | 3a (biggest, riskiest first) |
| `static/plot-hover.js` | 50 | 3a |
| `src/kayak/web/static/filters.js` | 43 | 3b |
| `static/feature-map.js` | 31 | 3b |
| `static/reach-map.js` | 19 | 3c (small tail, bundled) |
| `static/search-map.js` | 13 | 3c |
| `src/kayak/web/static/levels.js` | 7 | 3c |
| **+ rule flip** | — | 3d |

Biome's relevant rules:
- `noVar` (cat: suspicious, autofix: unsafe, NOT recommended) — flags every `var` and offers `let` autofix. Currently silent because not in biome.json.
- `useConst` (cat: style, autofix: safe, recommended) — flags `let` that should be `const`. Currently disabled via `"useConst": "off"` in biome.json.

**Recommended sequence:**

1. **3a, 3b, 3c — per-file modernization.** Enable `noVar: "warn"` temporarily, run `biome check --write --unsafe` on the file batch, review the resulting `var → let` diff for any reassignment-across-closures edge cases, then run `useConst` (also enabled temporarily) to upgrade non-reassigned `let` to `const`. Stage and commit per file batch.
2. **3d — rule flip.** Once all files are clean: flip `noVar` and `useConst` to `"error"` in `biome.json`'s rule block, remove the temporary `"useConst": "off"` line. One-line commits prevent future regressions.

Splitting into 4 commits (vs one big diff) keeps each batch reviewable in isolation and lets browser smoke-tests narrow the blast radius if a refactor introduces a real regression. **Phase 2 must land first** so each Phase 3 commit operates on strict-mode code — surfaces any latent scoping bug *before* the `var → let` conversion adds block-scoping on top.

**Risk:** `var` has function-scoped hoisting; `const`/`let` are block-scoped. Biome's `noVar` autofix converts to `let` unconditionally — won't catch:
- A `var` inside `if`/`for` that's referenced *after* the block ends — converting to `let` makes it `ReferenceError`. Common pattern in older JS; manual review is the only catch.
- A `var` reassigned across functions sharing a closure — biome's `useConst` correctly leaves it as `let`, but the initial `var → let` step still risks scope changes if the var is declared in a nested block but used outside it.

**Verification gate:** Phase 2 gate plus per-commit browser smoke-test of the file's primary use case (e.g. for the 3a commit covering `map.js`, walk through every map interaction on `map.html`). Side-by-side compare against `main` in a second tab — the modernized version must show identical behavior, zero console errors. This is harder than Phase 1's gate because the changes are more numerous and the autofix is less mechanical at the semantic level.

## Decisions to make

1. **`noRedundantUseStrict` — suppress.** Biome's rule misfires for classic scripts (the project's actual loading mode — every `.js` file is loaded via `<script src="…" defer>`, none via `type="module"`). The plan's recommended path is to suppress the rule in `biome.json` (`"suspicious": { "noRedundantUseStrict": "off" }`), keeping `plot-hover.js`'s existing directive in place and unblocking Phase 2's harmonization. The autofix-and-remove alternative isn't viable once Phase 2 lands — it would force the 10 new directives back into noisy-lint territory. **No real choice once Phase 2 is in scope; Phase 1 step 3 assumes suppression.**

2. **Do Phase 3 now, defer it, or skip it?** The work is mechanical but the verification gate (browser smoke-test of every JS-driven flow) is the labor-intensive part. Skip-or-defer reasoning: no current bug points at `var` semantics; CI doesn't fail on it; Phase 2's strict mode already catches the most dangerous failure mode (implicit globals); no other code in this project blocks on it. Do-it reasoning: while you're already in the JS files for Phase 2, the marginal cost of also modernizing them is low, and `useConst` enabled-everywhere stops the slow style drift. **Recommend deferring** unless you're already touching those files for an unrelated bug — Phase 2 captured most of the safety win, and Phase 3's verification gate is the bottleneck.

3. **If skipping Phase 3, leave `useConst` `"off"` or remove the entry?** Leaving `"off"` documents the conscious choice. Removing it lets biome's default (`recommended: true`) kick in next time biome upgrades and re-introduce the noise. Recommend: leave `"off"`. (No inline comment possible — biome.json rejects JSON comments. Rationale stays in this plan + git history.)

4. **`static/` vs `src/kayak/web/static/` split — consolidate or leave?** Out of scope for this plan: the split is functional, not historical. `static/` is PHP-served (copied wholesale by `_deploy_static_assets`); `src/kayak/web/static/` is content-hashed for cache-busting on the build-time HTML pages (`_JS_PATH` / `_FILTERS_JS_PATH` in `web/build/_shared.py`). Two different cache strategies → two different paths. Leave.

## Risks

- **The 4 files have no JS unit tests.** Neither does any other JS in the repo. Phase 1's auto-fixes are mechanical enough that a one-line code-review pass + browser smoke-test is the right level of safety. Phases 2 and 3 don't have that same easy-review property — both shift runtime semantics in ways static analysis won't fully cover; the browser-smoke-test gate is the bottleneck.
- **Use literal file paths in `biome.json` `includes`, not a glob.** A `"static/*.js"` glob would catch `static/leaflet.js` (144 KB minified vendored) and re-introduce the original noise that drove the explicit allow-list. The existing style is exact paths — match it for the four new entries.
- **`biome check --write` rewrites files in place.** Run on a clean working tree, eyeball every diff before staging, never `git add -A` blindly. The 6 unsafe autofixes — even though semantically defensible in this case — should each be reviewed visually before committing.
- **`useOptionalChain` autofix semantic shift.** `payload && payload.points` becomes `payload?.points`. If `payload` is `0`, `""`, `false`, `NaN`, or `null`, the new form returns `undefined` instead of the falsey value itself. The five flagged sites are `payload` (parsed JSON), `opts` (function argument), and `window.matchMedia` (function reference) — none realistically hold falsy primitives. Verify each site before committing; the audit has already confirmed these are safe, but `main` may shift before Phase 1 lands.
- **`noUnusedVariables` autofix renames a function parameter.** `catch (e) { return; }` becomes `catch (_e) { return; }`. The body discards the value; the rename is cosmetic. No risk.
- **The `biome.json` JSON file's diff in Phase 1 touches two keys** — `files.includes` (add 4, drop 1) and `rules.suspicious` (new key). Validate JSON syntax (`python3 -m json.tool < biome.json > /dev/null`) before staging — a stray comma would crash biome with an unexpected-error (verified by injecting a `//` comment during plan iteration; biome crashed at `crates/biome_rowan/src/ast/mod.rs:207`). CI would catch it on push, but pre-push is cheaper.

## Out of scope

- **JS unit tests.** Adding a JS test harness (jest, vitest, biome's own test runner) is its own decision. Useful eventually but heavier ROI than this plan's surface — the JS is mostly DOM-driven and would need jsdom or a real browser.
- **Bundling / minification.** The project ships unminified JS via PHP `<script src=...>` tags; there's no bundler. Out of scope.
- **TypeScript migration.** Not a current goal.
- **`leaflet.js` modernization / minification audit.** 144 KB minified vendored library; not project code. Out of scope.
- **`docs/map-color-tune/map3.js`** (348 LOC). A one-off developer tool inside `docs/`, not deployed to the site. Not worth linting; don't add to biome's includes.
- **PHP-layer changes** (`docs/PLAN_editor_security_review.md` / `docs/PLAN_php_layer_split.md` cover that surface).
- **Enabling biome's formatter** (`"formatter": {"enabled": false}` currently). Separate decision — would create a one-time bulk-format diff and require coordination with anyone editing JS in flight.
- **Switching biome's includes from explicit paths to a `static/*.js` glob.** The explicit-path style was specifically chosen to exclude `leaflet.js`; a glob would re-include it. Match the existing pattern.
- **Adding JS files to `.gitignore` cleanup**. `static/.DS_Store` is present; that's a macOS-specific concern outside this plan.

## Reproduce

Read-only commands to verify the current-state findings before Phase 1.

```bash
# Confirm the four files are *not* in biome.json's includes
grep -E '"includes"|static/.*\.js"|web/static/' biome.json

# List of files actually present
find static src/kayak/web/static public_html -maxdepth 1 -name "*.js" 2>/dev/null | sort

# Var count per file (matches the Current State table)
for f in static/feature-map.js static/gauge_picker.js static/plot-hover.js \
         static/map.js static/picker.js static/reach-map.js \
         static/search-map.js static/sw.js \
         src/kayak/web/static/filters.js src/kayak/web/static/levels.js; do
  [ -e "$f" ] && printf "%-45s %d\n" "$f" "$(grep -cE '^\s*var ' "$f")"
done

# Confirm strict-mode coverage (Phase 2 baseline): expect only plot-hover.js
grep -l "'use strict'" static/*.js src/kayak/web/static/*.js 2>/dev/null

# Confirm no `type="module"` JS loads anywhere (Phase 2 premise)
grep -rE 'script[^>]*type="?module"?' static/ src/kayak/ php/ public_html/ 2>&1 | head -5
# Expect: empty output

# What biome would surface if the four were linted (audit-only, restores config)
cp biome.json /tmp/biome.json.orig
python3 -c "
import json
with open('biome.json') as f: cfg = json.load(f)
cfg['files']['includes'] = [
    'static/sw.js', 'static/map.js', 'static/picker.js',
    'static/reach-map.js', 'static/search-map.js',
    'static/feature-map.js', 'static/gauge_picker.js', 'static/plot-hover.js',
    'src/kayak/web/static/levels.js', 'src/kayak/web/static/filters.js',
    'public_html/no_show_review.js',
]
with open('biome.json', 'w') as f: json.dump(cfg, f, indent=2)
"
biome check 2>&1 | grep -E "lint/|^Found "
cp /tmp/biome.json.orig biome.json
```

Expected output of the final biome run: `Found 7 warnings.` (the seven listed in §Current state) plus one `internalError/io` for the stale `no_show_review.js` path. If the count or set of rules changes, audit the new entries before Phase 1 — biome may have refined its rule set, or upstream JS may have shifted. The audit script *restores* `biome.json` before exiting; verify with `diff biome.json /tmp/biome.json.orig` after running.

## End state

After Phase 1:
- `biome check` reports **0 warnings**, "Checked 12 files in N ms."
- `biome.json`'s `files.includes` lists 10 JS file paths (8 under `static/`: `sw, map, picker, reach-map, search-map, feature-map, gauge_picker, plot-hover` — the last three newly added; 2 under `src/kayak/web/static/`: `levels.js` and the newly added `filters.js`) plus 2 CSS files. `no_show_review.js` is gone.
- `biome.json`'s `rules.suspicious` has one new line: `"noRedundantUseStrict": "off"` (assuming Decision 1 → suppress).
- `Makefile`'s `lint-js` target reads `biome check static/ src/kayak/web/static/` — directory traversal, no stale paths.
- `plot-hover.js` keeps `'use strict'`; `filters.js` and `plot-hover.js` have biome's `useOptionalChain`/`noUnusedVariables` autofixes applied; `gauge_picker.js` and `feature-map.js` are unchanged (already clean).

After Phase 2:
- All 10 hand-written JS files run in strict mode. `'use strict'` is now present in every IIFE (8 files) plus at the top of `sw.js`; `plot-hover.js`'s pre-existing directive is unchanged.
- No `biome.json` changes (the `noRedundantUseStrict` suppression from Phase 1 keeps the lint clean).
- Browser smoke-tests passed — no `ReferenceError`/`TypeError` introduced by strict semantics.

After Phase 3 (if undertaken):
- All 7 `var`-using files use `const`/`let` exclusively (236 `var` declarations gone).
- `biome.json`'s `rules.suspicious` adds `"noVar": "error"`; `rules.style` removes the `"useConst": "off"` line.
- CI catches any new `var` introduced by future JS edits.
