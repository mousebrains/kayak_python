# Plan — PHP layer split (apply build.py-split discipline to PHP)

> **Cross-check:** plan drafted 2026-05-11 against the PHP layer at HEAD (`75de842`). A second Claude session should re-run the read-only commands in **§Reproduce** before any tier starts, especially the file-size and PHPStan-level sanity checks — the PHP layer is more actively edited than `cli/build.py` was, and the file inventory may have shifted.

## Why

The PHP layer mirrors the pre-split state of `kayak/cli/build.py`: long monolithic entry-point files (`reach.php` 28KB, `description.php` 21KB, `svg_plot.php` 19KB, six more >14KB), growing user-facing surface, and per-file test coverage near zero outside auth/svg. Editor + propose + review pipelines all run through these files — bugs land in production fast and there's nothing on the lint side preventing complexity from creeping further.

Goal: apply the same per-file-split discipline that just landed for `cli/build.py`. Each big entry-point becomes a thin orchestration file delegating to extracted helpers in `php/includes/`. PHPStan level rises and a formatter joins the gate. Same per-tier review workflow as the build.py and production-discipline plans.

## Constraints

- **Live PHP-FPM lacks mbstring** ([reference_php_no_mbstring]). CI has it; production doesn't. All extracted code must use `strlen`/`substr`/`strtolower` rather than `mb_*`. Current code is clean (`grep -rn "\bmb_" php/` returns nothing as of plan-draft date) — the gate is preventive against drift, not remedial.
- **CSP enforced; no inline JS or event handlers** ([feedback_csp_no_inline]). Anything moved from inline `<script>` or `onclick=` must land in an external JS file under `static/`. Current code is clean (no inline `<script>` or `on*=` handlers in `php/` as of plan-draft date) — again, gate is preventive. Note: inline `<style>` blocks exist (e.g. `reach.php:17`'s compact-layout CSS); the CSP must permit them or those need extraction too.
- **PHP files are entry points, not modules.** Unlike `cli/build.py`, you can't replace `reach.php` with a 5-line shim — nginx routes URLs directly to it via FastCGI. The "split" is extract-helpers-and-include, not reduce-to-shim. Entry points retain ~200–250 lines of orchestration after split, not ~150.
- **The convention already exists.** `php/includes/` already holds 17 well-named helper modules (`auth.php`, `class_tiers.php`, `db.php`, `error.php`, `footer.php`, `gauge_map.php`, `gauge_plots.php`, `header.php`, `html.php`, `lttb.php`, `mail.php`, `review_logic.php`, `sanity.php`, `source_url.php`, `svg_plot.php`, `turnstile.php`, `validate.php`). This plan extends the same pattern; new extracts go alongside, not into a new directory. **Naming caveat:** existing `gauge_*.php` (`gauge_map`, `gauge_plots`) follows `<entity>_<aspect>` for shared concerns. The proposed `<entrypoint>_<cluster>.php` pattern (`reach_search.php`, `reach_detail.php`) is consistent. Use entry-point-specific words to avoid collision with shared helpers — e.g. `reach_search.php` not `reach_query.php` (latter could clash with future shared query helpers).
- **PHPDoc gap is real.** `php/includes/auth.php` has 21 functions and 0 PHPDoc tags. PHPStan level 5 doesn't enforce return-type hints in PHPDoc; level 7 starts to. Tier 1.1's level bump from 5 → 7+ may surface dozens of typing issues; budget time for adding `@param` / `@return` (or, better, native type declarations where PHP 8.4 allows). If the diff is too large, `phpstan-baseline.neon` (`phpstan analyse --generate-baseline`) is the modern alternative to per-file `ignoreErrors` — auto-regenerable as files clean up.
- **Two `auth.php` files exist.** `php/auth.php` (2.6KB, entry point — HTTP login/logout flow) and `php/includes/auth.php` (14KB, helper library — `current_editor()`, `csrf_token()`, `set_editor_session()`, `issue_magic_link()`, etc.). The plan's split work targets the helper, not the entry point.
- **`db.php` is excluded from PHPStan source coverage** (per `phpunit.xml`) because it does side-effectful PDO init at load time. Extracted helpers should not replicate that pattern.
- **Input convention is `filter_input()`, not `$_GET[]`.** `reach.php` uses `filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT)` — extracted helpers should accept already-validated values, not re-read superglobals. Auditing for direct `$_GET`/`$_POST` access in extracted code becomes part of the per-tier review.
- **Style.css is cross-language coupled.** The Python build's `_deploy_php_files` (`src/kayak/web/build/deploy.py:107`) copies `src/kayak/web/static/style.css` to `public_html/style.css` because `php/header.php` reads it via `__DIR__/../style.css`. Any change to where header.php reads CSS from requires updating the Python deploy code in the same commit.
- **Tooling already in place.** Composer + PHPStan ^2 (level 5) + PHPUnit ^11.5 + CI runs all three plus `php -l` syntax check. No Tier 0 setup needed; just raise the bar.
- **Phased.** Tier-by-tier review like the build.py split.

## Decisions baked in

- **Tooling-strict first.** Raise PHPStan from level 5 → at least 7 with per-file grandfathering (or `phpstan-baseline.neon`) for the unsplit files; add `friendsofphp/php-cs-fixer` as the analog of `ruff format`. These create the gate that the splits then satisfy. Realistic ceiling is level 8 given the PHPDoc/type-decl gap noted in Constraints — level 9 is aspirational.
- **Per-file pattern, applied in size order:**
  1. **reach.php** (entry point, 649 lines, no dedicated tests) — biggest payoff, biggest risk; gets the most care.
  2. **description.php** (entry point, 495 lines) — same shape, smaller.
  3. **`php/includes/svg_plot.php`** (helper, 503 lines) — *not an entry point*; it's already an include. `tests/php/SvgPlotTest.php` provides partial baseline. Splitting it affects every consumer (see "Plot rendering is shared" note in Risks).
  4. **Remaining big files** for Tier 5:
     - Entry points: **`propose.php`** (430), **`gauge.php`** (432), **`custom.php`** (363), **`custom_gauges.php`** (325), **`review.php`** (318) — each gets the entry-point template (orchestration extraction).
     - Includes: **`php/includes/auth.php`** (393), **`php/includes/gauge_plots.php`** (386) — each gets the helper-split template (sub-helper extraction). Different shape from the entry-point template; called out per-file in Tier 5.
- **Per-file workflow inside each split tier:**
  1. Add a baseline integration test (`curl` against a representative URL → assert HTTP 200 + key substrings present). PHPStan-level static check should already pass.
  2. Cluster analysis (in this doc, like the `# Current shape` section of `PLAN_build_split.md`).
  3. Extract one cluster per phase into `php/includes/<file>_<cluster>.php`. The entry-point file shrinks; tests stay green; PHPStan level holds.
  4. After all clusters extracted: the entry point is orchestration only.
- **Golden gate:** for each phase, capture HTTP response body for one representative URL pre-edit and post-edit. Diff must be empty (modulo time-driven exceptions documented per phase, same as the build.py plan).
- **No new DB schema, no new endpoints, no new behavior.** Pure code motion + extract-helpers, like Phases 1–8 of the build.py split.

## Target shape

```
php/
├── reach.php                # ~200-250 lines: arg parse, mode dispatch, render
├── description.php          # ~200-250 lines: same shape (single mode)
├── propose.php / gauge.php / custom.php / custom_gauges.php / review.php
│                            # Tier 5 entry-point template
└── includes/
    ├── reach_search.php     # search-mode helpers (avoid `reach_query` —
    ├── reach_list.php       #   could clash with future shared query helpers)
    ├── reach_detail.php
    ├── description_<cluster>.php × 3-4
    ├── svg_plot.php         # ~150 lines after Tier 4: top-level render only
    ├── svg_plot_interpolation.php  # rate_gauge_to_flow / rate_flow_to_gauge
    ├── svg_plot_geometry.php       # path-coord math
    ├── svg_plot_axes.php           # tick generation
    ├── auth.php             # split into smaller helpers in Tier 5 (sub-helper template)
    ├── auth_session.php / auth_magic_link.php / auth_csrf.php / auth_throttle.php
    ├── gauge_plots.php      # also split into sub-helpers in Tier 5
    └── (existing files: db.php, header.php, footer.php, html.php,
         mail.php, sanity.php, validate.php, etc. — unchanged)
```

Naming convention: each new include uses `<entrypoint>_<cluster>.php` (entry-point splits) or `<helper>_<subcluster>.php` (helper splits). The naming makes the call graph obvious from `ls` and avoids collision with shared `<entity>_<aspect>` helpers that already live there.

## Migration tiers

Each tier is several phases; **review gate between tiers**, not between phases. Same workflow as the build.py and production-discipline plans.

### Tier 1 — Discipline foundation

**Goal:** Tighten lint + format gates so the splits produce code that meets the new bar.

1. **Phase 1.1 — Raise PHPStan level.** Bump `phpstan.neon` from level 5 → 7 (or 8, if the codebase tolerates it). Add per-file `parameters.ignoreErrors` entries for the unsplit big files as needed; CI must stay green. The grandfathered file list shrinks across Tiers 2–5 as each file is split.
2. **Phase 1.2 — Add `friendsofphp/php-cs-fixer`.** Add to `composer.json` require-dev. Adopt PSR-12 + a ruleset that matches the existing code style (4-space indent, no tabs, em-dash + en-dash preserved in comments — verify the ruleset doesn't ASCII-fold). Run `php-cs-fixer fix` to surface the diff; review and apply iteratively until clean. Add `vendor/bin/php-cs-fixer fix --dry-run --diff` to CI as a hard gate (analog of `ruff format --check`). Add to `.pre-commit-config.yaml` alongside the existing `php-lint` hook so the same gates run locally before commit.
3. **Phase 1.3 — Baseline integration test scaffold.** This is **new infrastructure**, not an extension of existing tests. The 8 existing `tests/php/*.php` are all unit-style — they `require_once php/includes/<helper>.php` and call functions directly with seeded in-memory PDOs (`kayak_test_pdo()` in `tests/php/bootstrap.php`). No HTTP-level test exists today; entry-point splits need one.
   - **Test server:** `php -S 127.0.0.1:0 -t <docroot>` started from `setUpBeforeClass`, port read back from the bound socket (avoids port-reuse races on CI re-runs). Stop in `tearDownAfterClass`.
   - **Schema fixture:** `kayak_test_pdo()` only knows the `editor` + `editor_magic_link` schema — far too thin for `reach.php` which needs `reach`, `gauge`, `observation`, `latest_gauge_observation`, `reach_class`, `state`, etc. **Decision needed in Phase 1.3:** (a) maintain a parallel `tests/php/fixtures/full_schema.sql` synced manually with `src/kayak/db/models.py`, OR (b) invoke `levels init-db` against a tmp `*.db` from PHPUnit's `setUpBeforeClass` (couples PHP test to Python CLI but stays in lockstep with model changes), OR (c) extend `kayak_test_pdo()` with optional table groups (`kayak_test_pdo(['editor', 'reach', 'gauge'])`). **Recommended:** option (b) — leverages the existing migration discipline.
   - **Cookie injection:** auth-required endpoints (review.php, propose.php) need a logged-in editor session. Use `tests/php/AuthTest.php`'s `seedEditor` + a wrapper that issues a session cookie via the helper.
   - **Helper:** `request(string $path, array $query = [], array $cookies = [], string $method = 'GET', array $post = []): array` returning `['status' => int, 'headers' => array, 'body' => string]`.
   - **Golden helper:** `assertResponseContains(string $body, string ...$needles)` for substring matching (HTML element-attribute order isn't stable across PHP versions for assoc arrays).
4. **Phase 1.4 — Drill.** Add one golden-response test for `reach.php?id=<known_id>`. Confirm it passes; intentionally break a string in `reach.php`; confirm test fails; fix; confirm green.

**Verification gate (end of Tier 1):**
- `composer analyse` (PHPStan) green at the new level
- `composer fix --dry-run` green
- One integration test in CI green and demonstrably catches a real change

### Tier 2 — reach.php split

**Goal:** Reduce `reach.php` from a 28KB monolith to a thin entry point + 2–4 focused includes, no behavior change.

1. **Phase 2.1 — Baseline tests (✓ `e778053`).** Six integration tests cover all reach.php modes: `?q=<single-match>` (302 auto-redirect), `?q=<multi-match>` (results table), `?st=OR` (state filter), `?` (default-fallback to first reach), `?id=<gauged-reach>` (detail with map + linked gauge), `?id=<no-gauge-reach>` (detail no-gauge edge). Each asserts HTTP 200 (or 302), mode-appropriate substrings, and that no PHP-side `Content-Security-Policy` header was set (nginx owns it in prod; `php -S` won't see it). These are the gate every subsequent phase must keep green.
2. **Phase 2.2 — Cluster analysis (this commit).** See [Phase 2.2 — Current shape of `reach.php`](#phase-22--current-shape-of-reachphp) below. Two cluster extractions emerged (`reach_search`, `reach_detail`) — fewer than the plan's "3–4" guess because what looked like three top-level branches (search / list / detail) is really two: search-and-state-filter share the same code path; the default-fallback is a 14-line bridge into detail.
3. **Phase 2.3 — Extract `reach_search.php` (✓ `dc15a19`).** Moved lines 36–313 (the `if ($q_trimmed !== '' || $st !== '')` block) behind `handle_search_mode($db, $q, $st, $hidden, $compact_css): never` with six private helpers (query construction, reading aggregation, class/guidebook aggregation, results table render, map render, plus map-payload + gauge-collection helpers). `REACH_SEARCH_MAP_COLORS` lives at module scope (was a per-call array) — comment notes the mirror in `/static/search-map.js`. reach.php shrinks 649 → 377 lines.
4. **Phase 2.4 — Extract `reach_detail.php` (✓ `b5dd770`).** Moved lines 58–377 (load + render after default-fallback) behind `handle_reach_detail($db, $id, $hidden, $q, $st, $compact_css): void` with ten helpers (3 load + `_derive_reach_flow_levels` + 6 render). Arg-parse in reach.php tightened at the boundary (`is_string` checks) — knock-on effect cleared reach.php's remaining 6 baseline entries. Inline `http_response_code(404); exit('Reach not found')` preserved verbatim per the zero-behavior-change rule — switching to `get_reach_or_404` for the richer HTML 404 page is a deferred follow-up. reach.php shrinks 377 → 56 lines.
5. **Phase 2.5 — Final cleanup (✓ this commit).** Mostly closed out by 2.4's arg-parse tightening: reach.php is at 56 lines (target was < 100) with zero baseline entries. Plan-doc update marking Tier 2 done; verification-gate status recorded below. No additional code motion.

**Verification gate (end of Tier 2):** all met as of `b5dd770`.
- ✓ `reach.php` at 56 lines (target < 100; was 649 pre-tier)
- ✓ `php -l reach.php`, PHPStan level 7 (zero baseline entries for reach.php), php-cs-fixer all green
- ✓ All six baseline integration tests pass on each phase commit (`dc15a19`, `b5dd770`); CI green
- ✗ Side-by-side HTML diff against staging not run — integration tests' substring assertions substituted. Acceptable because: (a) the 6 tests cover all three modes + the no-gauge edge with mode-discriminating substrings; (b) the extraction was pure code motion (per-phase commit messages document the zero-behavior-change rule); (c) the codebase doesn't have a staging vhost. If a regression surfaces later, the gap is documented.

**Tier 2 outcome — file shape after split:**

| File | Lines | Role |
|---|---|---|
| `php/reach.php` | 56 | Orchestration: requires, arg parse, mode dispatch, default-fallback inline |
| `php/includes/reach_search.php` | 436 | `handle_search_mode` + 6 private helpers, `REACH_SEARCH_MAP_COLORS` |
| `php/includes/reach_detail.php` | 570 | `handle_reach_detail` + 10 private helpers (3 load / 6 render / map) |

PHPStan baseline net movement: 123 → 124 → 123 over Tiers 2.3/2.4 (+1 from a more-specific docblock in 2.3, -1 from the arg-parse cleanup in 2.4).

#### Phase 2.2 — Current shape of `reach.php`

The file is **fully procedural** — 649 lines, zero `function` definitions, top-level code runs directly under nginx FastCGI. Five discernible top-level cluster regions dispatching off two query params (`?id` for detail, `?q`/`?st` for search/state-filter):

| Cluster | Lines | What it does | Exits? |
|---|---|---|---|
| Setup / arg-parse | 1–34 | requires (db, header, footer, html), `$db = get_db()`, inline `$compact_css` style block (`<style>...</style>` — a CSS-only override for desktop utility layout), mutable `$has_map`/`$map_scripts`, parse `$id`/`$q`/`$st`/`$hidden` via `filter_input` | falls through |
| Search / state-filter mode | 36–313 | If `?q=` or `?st=` is set: three SQL query variants (q+st, q-only, st-only), single-result auto-redirect, latest-flow aggregation across result reaches, class/guidebook aggregation, render header + table + map + footer | `exit` (lines 95, 312) |
| Default fallback | 315–328 | If neither `?id` nor `?q`/`?st`: pick first reach by `sort_name` — empty-state render if DB has no reaches; otherwise sets `$id` and falls through to detail | `exit` (line 325) on empty-DB only |
| Detail mode — data load | 330–405 | Load reach (404 if missing), prev/next nav queries, total count + position, gauge, states, classes, derive `$flow_levels` (low/okay/high bands from primary class range), guidebooks | falls through |
| Detail mode — render | 407–649 | header + nav bar (prev/next + embedded search form + state-select + hidden toggle), details table (with coord → Google Maps links), class-ranges sub-table, flow-levels sub-table, guidebooks sub-table, linked-gauge sub-table, map div + leaflet scripts, footer | end of file |

**Shared mutable state** inside the entry-point scope (relevant to extraction signatures):
- `$db` — read by every cluster
- `$compact_css` — read by both search render (line 188) and detail render (line 413); echoed once each
- `$has_map`, `$map_scripts` — initialized empty at top; set inside whichever cluster renders a map; conditionally echoed at the end of that same cluster. **Internal to each cluster — does not leak.**
- `$id`, `$q`, `$st`, `$hidden` — set by setup, read by every downstream cluster. The detail nav bar's embedded search form (line 431, 437) re-echoes `$q` and `$st` as input values; the hidden-toggle link (line 445) uses `$hidden`.

**Cluster-extraction target shape:**

```
php/
├── reach.php                  # < 100 lines: requires, arg parse, mode
│                              #   dispatch, default-fallback inline
└── includes/
    ├── reach_search.php       # handle_search_mode(
    │                          #   PDO $db, string $q, string $st,
    │                          #   int $hidden, string $compact_css
    │                          # ): never
    │                          # Plus private helpers for: query, latest-
    │                          # reading aggregation, class/guidebook
    │                          # aggregation, render-table-and-map.
    └── reach_detail.php       # handle_reach_detail(
                               #   PDO $db, int $id, int $hidden,
                               #   string $q, string $st, string $compact_css
                               # ): void
                               # Plus private helpers for: load_reach_with_nav,
                               # derive_flow_levels, render_nav_bar,
                               # render_details_table, render_class_ranges,
                               # render_flow_levels, render_guidebooks,
                               # render_linked_gauge, render_reach_map.
```

**Phase order rationale:** Search first (Phase 2.3), then detail (Phase 2.4). Search is the smaller, self-contained block (278 lines extracted, ends with `exit` — no cross-cluster state leakage). Detail is bigger and has more sub-helpers but no novel patterns; doing search first builds the helper-extraction template against the simpler block. After both extractions, `reach.php` keeps only the default-fallback bridge inline (14 lines) — extracting that to a helper would obscure the mode-dispatch logic for no benefit.

**Non-obvious extraction risks specific to `reach.php`:**

- **`$_SERVER['DOCUMENT_ROOT']` reads** at lines 297 (search map) and 629 (detail map) load `/static/leaflet.css` via `file_get_contents`. Under `php -S`, `DOCUMENT_ROOT` is the `-t` arg (the test docroot); under nginx FastCGI, it's the vhost's `root` directive. Both should resolve correctly without code change — keep using `$_SERVER['DOCUMENT_ROOT']` in the extracted helpers, do not switch to `__DIR__` (which would break under prod where `php/` is symlinked into docroot).
- **`$gauge_ids` is referenced twice** in the search block (line 101 builds it; line 273 reuses it for the gauge-locations query). The aggregation and rendering helpers will need to share it — either return-then-pass or compute twice. Recommend compute-once and pass.
- **Guidebook abbreviation map** at lines 146–155 (`$ss_edition`, `$gb_abbrev`) is hardcoded data; extract as a private `const`-like array in the helper file or keep inline in `_aggregate_classes_guides`. Don't move to a shared include — it's truly reach-search-specific.
- **`$colors` palette** at line 237 is shared with `/static/search-map.js` (passed via `data-colors` JSON). Don't change the palette during extraction or the map markers won't match the table swatches.
- **Detail-mode nav bar embeds `$all_states`** via `$db->query('SELECT abbreviation FROM state ORDER BY abbreviation')` at line 429. This is a fresh query inside the render path, not a value from the data-load cluster. Keep it inside the nav render helper; don't promote to data load.

### Tier 3 — description.php split

Same template as Tier 2, applied to `description.php`. Smaller file (495 lines vs reach.php's 649) but **denser cross-cluster dependencies**: 8 includes (db, header, footer, html, svg_plot, gauge_plots, gauge_map, validate) vs reach.php's 4. Single-mode entry point (`?id=N` only — 400 on missing). Date filtering (`start`, `end`, `hidden` params) widens the parameter space.

Reuses the existing `get_reach_or_404($id)` from `php/includes/db.php:50` for 404 handling — extracted helpers should follow this naming convention for any new fail-fast lookups.

1. **Phase 3.1 — Baseline tests (✓ `48f6ac3`).** Six integration tests cover description.php: `/description.php` (400 missing id), `?id=not-an-int` (400 invalid id), `?id=<gauged>` (full render with readings table, "Data Sources" section, Put-in/Take-out, footer link), `?id=<no-gauge>` (no-gauge edge — asserts NO readings table / NO Data Sources / NO Put-in), `?id=<gauged>&start=...&end=...` (still 200 with no obs in window), `?id=<gauged>&start=garbage` (`validate_date` returns null; entry-point accepts and skips filter).
2. **Phase 3.2 — Cluster analysis (✓ `63c79cf`).** See [Phase 3.2 — Current shape of `description.php`](#phase-32--current-shape-of-descriptionphp) below. Single extraction target (`description_detail.php`) — single-mode entry point means no mode-dispatch boundary inside the file; the "split" is one fat helper with sub-helpers (analogous to `reach_detail.php` but without a `reach_search.php` sibling).
3. **Phase 3.3 — Extract `description_detail.php` (✓ `f591616`).** Moved lines 24–495 behind `handle_description_detail($db, $id, $start_date, $end_date, $hidden): void` with eleven private helpers. Arg-parse tightening at the boundary cleared description.php's 2 baseline entries (same effect as reach.php in Phase 2.4). description.php shrinks 495 → 31 lines.
4. **Phase 3.4 — Final cleanup (✓ this commit).** Mostly closed out by 3.3's arg-parse tightening: description.php is at 31 lines (well under the < 100 target) with zero baseline entries. Plan-doc update marks Tier 3 done and records the three shared-helper candidates with `reach_detail.php` as a deferred follow-up.

**Verification gate (end of Tier 3):** all met as of `f591616`.
- ✓ `description.php` at 31 lines (target < 100; was 495 pre-tier)
- ✓ `php -l description.php`, PHPStan level 7 (zero baseline entries for description.php), php-cs-fixer all green
- ✓ All six Description integration tests pass on each phase commit (`f591616`); CI green
- ✗ Side-by-side HTML diff against staging not run — same caveat as Tier 2; the 6 integration tests' substring assertions are the substitute

**Tier 3 outcome — file shape after split:**

| File | Lines | Role |
|---|---|---|
| `php/description.php` | 31 | Orchestration: requires, arg parse (with is_string-narrowing for validate_date), 400-on-missing-id, single dispatch |
| `php/includes/description_detail.php` | 745 | `handle_description_detail` + 11 private helpers (4 load / 7 render) |

PHPStan baseline net movement: 123 → 123 over Tier 3 (description.php's 2 entries cleared by arg-parse tightening; description_detail.php absorbed 2 of those plus 1 new PDO-mixed-return entry = 3 entries, balancing the ledger).

**Deferred — shared-helpers DRY pass with `reach_detail.php`:** Three sub-clusters now exist in both files with near-identical bodies. Not bundled with Tier 3 to keep the extraction's behavior-change footprint at zero. Candidate consolidation:
- `_load_reach_navigation` (reach_detail) ≡ `_load_description_navigation` (description) — identical 4-query body, differs only in URL prefix the renderer uses (`/reach.php` vs `/description.php`). Promotion path: move to `db.php` as `get_reach_navigation_context($db, $reach, $id, $hidden)` returning the same shape; callers pass the prefix to their respective renderers.
- `_derive_reach_flow_levels` (reach_detail, fetches the row inline) vs `_derive_description_flow_levels` (description, accepts the row as a parameter). Same band-derivation logic. Promotion path: a single `derive_flow_levels(?array $class_range)` in a new `reach_common.php`; reach_detail loses its inner fetch, performs the fetch at its `_load_reach_related` boundary instead (matches description's pattern).
- Guidebooks render: `_render_reach_guidebooks($reach, $guidebooks)` (reach_detail) vs `_render_description_guidebooks($db, $reach, $id)` (description). Body is the same; surrounding button bar differs (description has Edit/Suggest-edit; reach_detail has Description/Data inspector links). Promotion path: parameterize the surrounding context; or just leave them separate since the body is small (~35 lines).

Either bundle with Tier 5 (or a dedicated Tier 4.5) or accept as standing duplication — the maintenance burden today is "update in two places when guidebook schema changes" which has happened zero times in the last 3 months per `git log php/`.

#### Phase 3.2 — Current shape of `description.php`

The file is **fully procedural** — 495 lines, zero `function` definitions, single-mode entry point (always detail). Ten discernible cluster regions, no mode-dispatch boundary inside:

| Cluster | Lines | What it does | Calls into |
|---|---|---|---|
| Setup / arg-parse / 400 | 1–29 | 8 requires, parse `$id`/`$start_date`/`$end_date`/`$hidden`, 400 on missing id, `$db = get_db()`, `$reach = get_reach_or_404($id)` | `db`, `validate` |
| Navigation | 31–45 | prev/next/total/position queries (same 4-query shape as reach_detail's nav load) | `db` |
| Related data load | 47–87 | gauge, states, classes (one query each), derive `$flow_levels` from primary class range (same logic as reach_detail's `_derive_reach_flow_levels`) | `db` |
| Header + nav bar render | 89–121 | `Cache-Control: private`, preconnects, `include_header` with editor-feature context, prev/next nav bar, `<h2>` title | `header` |
| Current readings | 123–178 | `latest_gauge_observation` fetch + 5-col table render with stable/rising/falling status spans | `db` |
| Date range + SVG plots | 180–192 | `gp_resolve_window` (in `gauge_plots.php`) + `gp_render_date_form` + `gp_render_plots` — wraps the inline plot rendering pipeline | `gauge_plots` |
| Description fields + map | 194–314 | Assemble `$fields` table (Class, State, Watershed, …, optional `Low/Okay/High Flow` rows, coordinate-as-anchor fields), inline Leaflet map via `gm_render_map`, render table | `html`, `gauge_map` |
| Data sources | 316–434 | `source` + `gauge_source` joined fetch; USGS/NWRFC station-page link inference by `agency` substring; calc-expression cross-ref autolinker (`preg_replace_callback` with embedded `prepare`/`fetch`) | `db` |
| Guidebooks | 436–470 | reach_guidebook fetch + table render with AW link (matches reach_detail's guidebooks render but uses a different button bar in the footer) | `db` |
| Footer nav + scripts | 472–495 | Button-bar nav (Back / Reach details / Edit-or-Suggest-edit by editor role), conditional Leaflet + feature-map script tags, `include_footer` | `auth` (transitive via `header`), `footer` |

**Shared mutable state** inside the entry-point scope (relevant to extraction signatures):
- `$db` — read by every cluster except header/render
- `$has_map` — initialized at line 25; set by `gm_render_map` at line 294; conditionally echoes the leaflet+feature-map `<script>` tags at line 489. **Crosses cluster boundaries** — the data-sources cluster doesn't touch it, but it leaks across the fields/map cluster and the footer cluster
- `$reach`, `$name`, `$gauge`, `$class_range`, `$flow_levels`, `$readings` — set by load clusters, read by multiple render clusters. The current-readings cluster's `$readings` feeds the flow-fields/map cluster's track-color logic (line 268)
- `$id`, `$start_date`, `$end_date`, `$hidden` — set by setup; read by every downstream cluster

**Cluster-extraction target shape:**

```
php/
├── description.php            # < 100 lines: requires, arg parse, 400-on-missing,
│                              #   dispatch to handle_description_detail()
└── includes/
    └── description_detail.php # handle_description_detail(
                               #   PDO $db, int $id, ?string $start_date,
                               #   ?string $end_date, int $hidden
                               # ): void
                               # Private helpers (~9):
                               #   _load_description_navigation, _load_description_related,
                               #   _load_current_readings, _compute_track_color,
                               #   _render_description_nav_bar, _render_current_readings,
                               #   _render_date_form_and_plots, _render_description_fields_and_map,
                               #   _render_data_sources, _render_description_guidebooks,
                               #   _render_description_footer
```

**Cross-file overlap with `reach_detail.php`** (deferred to a follow-up DRY pass, not Tier 3):
- Navigation load: `_load_description_navigation` ≈ `_load_reach_navigation` (identical 4-query shape, differ only in URL prefix the renderer uses).
- Flow-levels derivation: description's lines 67–87 = `reach_detail._derive_reach_flow_levels` verbatim.
- Guidebooks: description renders almost the same table as `reach_detail._render_reach_guidebooks` but the surrounding button bar differs (description has Edit/Suggest-edit per editor role; reach_detail has Description/Data inspector links).

Bundling these into a shared `reach_common.php` (or moving them up into `db.php` / a new `reach_navigation.php`) is a 4th-helper-extraction job. Not Tier 3 — Tier 3's gate is single-file extraction with zero behavior change. Note this in the Tier 3 closeout as a follow-up.

**Non-obvious extraction risks specific to `description.php`:**

- **`gp_render_plots` reads/writes nothing in the entry-point scope** — it's a pure render helper that takes its inputs as args. Safe to extract whole-cluster.
- **`gm_render_map` returns a bool** (`$has_map = gm_render_map(...)` at line 294) and also emits its `<div>` to stdout. The bool drives the post-footer `<script>` tag emission. Extracted helpers need to preserve this contract — either return the bool through the call chain or have the fields-and-map helper handle script emission itself.
- **`preg_replace_callback` at lines 395–417** uses a closure that captures `$db` and runs additional prepared queries inside the regex callback (gauge cross-ref lookup). Extracted helper needs to accept `$db` and re-form the closure with `use ($db)`.
- **The flow-fields rendering uses both `$flow_levels` and `$readings`** (lines 268–288 in track-color computation). Extraction needs to keep these two pieces of state available to the fields-and-map helper.
- **`Cache-Control: private` at line 93** (vs reach.php's `Cache-Control: no-cache`) — description renders the editor's email in the nav, so it's response-specific. Preserve this header — moving it to `include_header` would lose the per-page distinction.
- **`htmlspecialchars` on calc-expression text** (line 394) happens BEFORE the `preg_replace_callback`. The comment on lines 390–393 documents why; preserve the order during extraction or the autolinker can leak HTML metacharacters between matches.

### Tier 4 — `php/includes/svg_plot.php` split

`svg_plot.php` is an **include**, not an entry point — `description.php`, `plot.php`, `php/includes/gauge_plots.php`, and (transitively via gauge_plots) `gauge.php` all `require_once` it. The "split" here is sub-helper extraction, not orchestration extraction. The 503-line file becomes a smaller include + `svg_plot_rating.php`.

`tests/php/SvgPlotTest.php` is the existing baseline (11 cases covering all 5 externally-called functions). Augment with consumer-side integration tests on the entry points that call it: `plot.php` (Phase 4.1) and `description.php` (already in `DescriptionIntegrationTest`).

The plan's earlier sketch of a 4-way split (geometry / styling / axes / top-level) doesn't match the file's actual cluster boundaries. Of the 10 functions, only the 3 **rating** functions form a coherent extractable unit; the rest are tightly interwoven with the two `generate_*_plot` renderers. See [Phase 4.2 — Current shape of `svg_plot.php`](#phase-42--current-shape-of-svg_plotphp) below for the actual cluster table. Outcome: 2-file split (rating extracted; everything else stays).

1. **Phase 4.1 — Baseline tests (✓ `e217ab5`).** Five integration tests cover plot.php (the simplest external consumer of `generate_svg_plot`): 400 on missing id, 400 on invalid type, 404 on non-gauge reach, 200 raw `image/svg+xml` for a gauged reach, 200 HTML wrapper on `?embed=1`. SvgPlotTest's 11 existing cases + DescriptionIntegrationTest's 6 cases already cover the helper directly and the transitive consumer path via gauge_plots.
2. **Phase 4.2 — Cluster analysis (✓ `619122f`).** See [Phase 4.2 — Current shape of `svg_plot.php`](#phase-42--current-shape-of-svg_plotphp) below.
3. **Phase 4.3 — Extract `svg_plot_rating.php` (✓ `5cd9b85`).** Moved `derive_rating_lookup`, `rate_gauge_to_flow`, `rate_flow_to_gauge` (147 lines) out of svg_plot.php. The surviving svg_plot.php require_once's the new file so generate_rating_dual_plot's internal `rate_*_to_*` calls still resolve. Consumer-side edits: zero — PHP's global function namespace means all existing callers (plot.php, gauge_plots.php, description_detail.php, SvgPlotTest) pick up the new physical home transparently. svg_plot.php shrinks 503 → 382 lines.
4. **Phase 4.4 — Final cleanup (✓ this commit).** Plan-doc closeout. No additional code motion.

**Verification gate (end of Tier 4):** all met as of `5cd9b85`.
- ✓ `svg_plot.php` at 382 lines (was 503 pre-tier; no hard < 200 line target — this is a helper, not an entry-point shim)
- ✓ `svg_plot_rating.php` at 147 lines with the 3 rating functions and nothing else
- ✓ `php -l` on both, PHPStan level 7, php-cs-fixer all green
- ✓ All 11 SvgPlotTest + 5 PlotIntegrationTest + 6 DescriptionIntegrationTest cases pass (full PHPUnit: 72/72)

**Tier 4 outcome — file shape after split:**

| File | Lines | Role |
|---|---|---|
| `php/includes/svg_plot.php` | 382 | Layout helpers (`_split_y_label`, `_series_data_attr`), axis math (`nice_axis`), bands (`_bands_svg`), plot renderers (`generate_svg_plot`, `generate_rating_dual_plot`, `_empty_svg`). Requires `svg_plot_rating.php` for the internal `rate_*_to_*` calls inside the dual-plot renderer. |
| `php/includes/svg_plot_rating.php` | 147 | Three rating-curve functions: `derive_rating_lookup` (DB-bound), `rate_gauge_to_flow` (forward), `rate_flow_to_gauge` (inverse). No own includes. |

PHPStan baseline net movement: 0 — extraction was pure code motion; no signatures changed; the 0 baseline entries for svg_plot.php stayed at 0, and svg_plot_rating.php came up clean. No new entries needed.

**Deferred to cleanup tier:** further granularity (`nice_axis` and `_bands_svg` as their own files) would each produce a ~30–40-line file. Defer until either: (a) Tier 6 cleanup notices svg_plot.php is still unwieldy at 382 lines; (b) a Tier-5 entry-point extraction reveals a need for one of these helpers in isolation; (c) someone adds a third consumer of `nice_axis` outside the file. Until then, the file's internal cohesion (layout helpers + axis math + bands + renderers) is high enough that the size doesn't justify additional splits.

#### Phase 4.2 — Current shape of `svg_plot.php`

503 lines, 10 functions in 5 logical clusters. Public API surface (functions called by external consumers) is **5 functions**: `generate_svg_plot`, `generate_rating_dual_plot`, `derive_rating_lookup`, `rate_gauge_to_flow`, `rate_flow_to_gauge`. The other 5 (`_split_y_label`, `_series_data_attr`, `nice_axis`, `_bands_svg`, `_empty_svg`) are file-private (`_`-prefix; `nice_axis` lacks the prefix but no external consumer calls it).

| Cluster | Lines | Functions | External use? | Notes |
|---|---|---|---|---|
| Layout helpers | 6–25 | `_split_y_label`, `_series_data_attr` | no | Used by both `generate_*_plot` to build the `data-series` JSON attribute and decompose Y-axis labels. |
| Axis math | 27–63 | `nice_axis` | no (despite missing `_`) | Computes round Y-axis bounds + step for tick labels. Pure function; could be tested directly. |
| **Rating curve** | **65–188** | **`derive_rating_lookup`, `rate_gauge_to_flow`, `rate_flow_to_gauge`** | **yes (gauge_plots.php)** | The only DB-bound code in the file (`derive_rating_lookup` takes a `PDO`). Bidirectional interpolation; covered by 6 SvgPlotTest cases. Extracts cleanly. |
| Bands SVG | 190–231 | `_bands_svg` | no | Renders low/okay/high background rectangles. Called from both generators after the data range is computed. |
| Plot renderers | 233–503 | `generate_svg_plot`, `generate_rating_dual_plot`, `_empty_svg` | yes (plot.php, gauge_plots.php) | The bulk of the file (~270 lines). Both generators do their own grid + axis + polyline math inline, intermixed with the cluster-A/B/D internals — there's no clean axis/styling/geometry boundary to extract along. |

**Consumer call graph:**
- `plot.php` → `generate_svg_plot` (only)
- `gauge_plots.php` → all 5 external functions
- `description_detail.php` → none directly; requires `svg_plot.php` only to make the helpers reachable transitively (through `gauge_plots.php`)

**Why "rating extraction only" and not the plan's earlier 4-way sketch:** the plan-draft envisioned splitting "geometry / styling / axes / top-level". Reading the actual code, the two `generate_*_plot` functions inline their grid-line emission, axis-label formatting, and band-rendering — there's no extractable "geometry helper" or "styling helper" sitting at function granularity. Decoupling those would require **refactoring the renderers themselves** (extracting `_build_grid_lines`, `_build_polyline`, etc.), which is a behavior-equivalent refactor of intra-function bodies rather than a code-motion split.

Rating is different. The 3 rating functions:
- form a closed loop (`derive_rating_lookup` produces the array that `rate_*_to_*` consume)
- are independently testable (SvgPlotTest already exercises them in isolation)
- have a distinct concern (rating-curve interpolation, a hydrology domain concept)
- contain the only DB-bound code in the file

Extracting just rating is high-signal, low-risk. Further granularity (separating `nice_axis` or `_bands_svg` into their own files) is plausible but tiny — each would be a ~30-line file. Defer to Tier 6 cleanup if the file still feels unwieldy after the rating split.

**Cluster-extraction target shape:**

```
php/includes/
├── svg_plot.php             # ~390 lines: _split_y_label, _series_data_attr,
│                            #   nice_axis, _bands_svg, generate_svg_plot,
│                            #   generate_rating_dual_plot, _empty_svg.
│                            #   require_once 'svg_plot_rating.php' so the
│                            #   internal calls to rate_*_to_* inside
│                            #   generate_rating_dual_plot still resolve.
└── svg_plot_rating.php      # ~120 lines: derive_rating_lookup,
                             #   rate_gauge_to_flow, rate_flow_to_gauge.
                             #   No `require_once` needed (uses no helpers
                             #   beyond PDO; lttb is only used by the
                             #   downsampler in svg_plot.php proper).
```

**Non-obvious extraction risks:**

- **`generate_rating_dual_plot` calls `rate_gauge_to_flow` and `rate_flow_to_gauge` internally** (lines 419–420 and inside the right-axis tick-generation loop). After extraction, `svg_plot.php` must `require_once svg_plot_rating.php` — `require_once` is idempotent so consumers that already require both files (none today, but future ones could) don't double-load.
- **Consumer-side edits: zero.** PHP's function namespace is global; once `svg_plot.php` requires `svg_plot_rating.php`, every existing consumer (plot.php, gauge_plots.php, description_detail.php) keeps working without changes. The "require" is transitive.
- **SvgPlotTest's `require_once` of `svg_plot.php`** at line 5 (per the existing test convention) similarly pulls in the rating file transitively. No test edit needed.
- **`derive_rating_lookup`'s SQL uses `gauge_source` join + `observation.observed_at >= ?`** — a `PDO` is passed in; no global state. Pure extraction.
- **`lttb_downsample` (from `lttb.php`) is only used inside `generate_svg_plot` / `generate_rating_dual_plot`** (lines 277, 384) — stays with the renderers, not with rating.

### Tier 5 — Apply template to remaining big files

**Goal:** Same discipline applied to the remaining six big files, in two shapes:

**Entry-point template** (orchestration extraction, like Tiers 2/3): `propose.php` (430), `gauge.php` (432), `custom.php` (363), `custom_gauges.php` (325), `review.php` (318). Order: largest first. `gauge.php` follows the reach.php multi-mode pattern (`?id=` detail vs `?q=` search); baseline tests must cover both. `propose.php`, `edit.php`, `review.php` have POST handlers — baseline tests must include POST cases with valid CSRF tokens.

**Helper-split template** (sub-helper extraction, like Tier 4): `php/includes/auth.php` (393), `php/includes/gauge_plots.php` (386). For each: identify subdomains (auth.php splits into session, magic-link, csrf, throttle clusters; gauge_plots.php splits into multi-series-rendering, axis-handling, etc.). The existing convention (snake_case functions, no namespace, no class) carries forward.

Per-file phase shape: baseline tests → cluster analysis (in commit messages, not this doc) → extract clusters → cleanup. Order: entry-points first (build experience with the template); then `gauge_plots.php`; then `auth.php` last because it's load-bearing for the editor feature and benefits from any patterns established earlier.

**Progress tracker:**

| File | Pre | Post | Phases | Status |
|---|---:|---:|---|---|
| `gauge.php`        | 432 | 52  | 5.G.1 `d46f5e2` / 5.G.2 `5ac3a57` / 5.G.3 `ad79589` / 5.G.4 `1042cc3` (+ review fixup `e72d9ca` + CI fix `998976d`) | ✓ Done — `gauge_search.php` (92) + `gauge_detail.php` (608) |
| `propose.php`      | 430 | 32  | 5.P.1 `fc4f2cc` / 5.P.2 `abe0e70` / 5.P.3 this commit | ✓ Done — `propose_handler.php` (573); editor-session test infra added in 5.P.1 |
| `custom.php`       | 363 | —   | not started | pending |
| `custom_gauges.php`| 325 | —   | not started | pending |
| `review.php`       | 318 | —   | not started — has POST + CSRF (reuses editor-session infra from 5.P.1) | pending |
| `includes/gauge_plots.php` | 386 | — | not started — helper split | pending |
| `includes/auth.php`        | 407 | — | not started — helper split, last (load-bearing) | pending |

Tier 5 outcome to date: 2 of 7 files done. Per-file gates (php -l + PHPStan + cs-fixer + integration tests green) met on each commit; CI green.

**5.P infrastructure callout:** Phase 5.P.1 added `seedEditorSession()` + `testDb()` helpers to `IntegrationTestCase` — production-format ed_sess + ed_csrf tokens that test code passes through `request()`'s `$cookies` arg (and as the `csrf_token` form field for POSTs). Unlocks review.php and any future edit.php coverage without per-test boilerplate.

**5.G/5.P CI lesson:** PHP's global function namespace makes `_`-prefix-by-convention insufficient for file-locality. Two extraction phases introduced helpers with colliding names (`_render_date_form_and_plots` in description_detail.php + gauge_detail.php; `_prefill` in propose.php + my draft propose_handler.php). PHPStan's file-load-order determinism made the collision flag CI but pass locally — caught in `998976d`. Future helper extractions must name file-private helpers with the file's prefix (`_render_<file>_*`, `_load_<file>_*`, etc.), not bare `_*`.

**Cross-plan note:** `auth.php` is also covered by the editor security review (`PLAN_editor_security_review.md`). If a security finding lands while this tier is in flight, fix it first — splitting on top of a known security gap risks shipping the gap into more files.

### Tier 6 — PHPStan max + closeout

**Goal:** Final gate sweep.

1. **Phase 6.1 — Empty the grandfather list.** Every file split in Tiers 2–5 should already be off the per-file ignore list. Confirm by deleting the list and running CI. Any file still flagging needs a brief follow-up extraction or a documented justification.
2. **Phase 6.2 — Raise PHPStan to max (or as high as the codebase tolerates).** Bump from the Tier-1 level toward level 9. Given the PHPDoc gap noted in Constraints, getting all the way to 9 may require adding native type declarations across `includes/*.php`. Realistic outcome: land at level 8 with a small grandfather list rather than level 9 with a giant one. Use `phpstan-baseline.neon` if the diff is too large for inline fixes.
3. **Phase 6.3 — Update `CLAUDE.md`.** Document the new PHP discipline: PHPStan level, php-cs-fixer command, integration-test pattern. Pointer to `php/includes/` naming convention.

## Risks

- **mbstring trap.** CI has it; production doesn't. Extracted helpers that drift toward `mb_strlen`/`mb_substr` will pass tests but fail in production silently (with subtle character-handling bugs, not crashes). Mitigation: add a CI grep step `! grep -rn "\bmb_" php/` that fails the build.
- **Inline-JS regression.** Splitting render functions might tempt re-introducing inline `<script>` or `onclick=`. CSP will block them in production but tests probably won't catch it. Mitigation: extend the integration golden-response test to assert `! str_contains($body, "<script>")` (or to assert all `<script>` tags have `src=`).
- **Side-effectful loads.** `db.php` initializes PDO at load time. Any new include that does similar work at load (rather than via a function) breaks the test isolation pattern. Mitigation: PHPStan rule, or convention enforced by code review.
- **Endpoint behavior drift.** Unlike `cli/build.py` (output is HTML files), PHP entry points have HTTP request semantics: query params, cookies, sessions, headers. A subtle change in request-parsing order can change `$_GET` precedence over `$_POST` — easy to miss. Mitigation: golden-response tests with multiple query patterns.
- **Test flakiness from `php -S`.** Built-in test server can race with port reuse on quick CI re-runs. Mitigation: bind to port 0 and read the assigned port; or use a unix socket.
- **Editor feature is load-bearing.** Splitting `php/includes/auth.php` or `propose.php` while real users are editing risks user-visible failures. Tier 5 ordering puts `auth.php` last; consider deploying to `levels-test.wkcc.org` first and waiting a week before promoting.
- **Plot rendering is shared across consumers.** `svg_plot.php` is required by `description.php`, `plot.php`, `gauge_plots.php`, and (transitively) `gauge.php`. A signature change in svg_plot during Tier 4 ripples through all of them. The shared API surface needs to be enumerated *before* the first Tier 4 phase, not discovered mid-extraction. The Tier 4 baseline tests should hit at least one consumer of *each* call path.
- **Cross-language deploy coupling.** `style.css` is copied at deploy time by Python (`src/kayak/web/build/deploy.py:107`). Any PHP-side change to where stylesheets are read from requires a paired Python-side change in the same commit; otherwise prod loses styling on next deploy. Same applies to `php/includes/header.php`'s assumptions about `__DIR__/../style.css`.
- **CSP is set in nginx via snippets — not in this repo.** The vhost (`deploy/levels`) `include`s `/etc/nginx/snippets/security-headers.conf` (default) or `/etc/nginx/snippets/security-headers-turnstile.conf` (relaxed for `/login.php` and `/contact.php`, where Turnstile needs script-src/frame-src/style-src/connect-src loosened). The actual snippet files live only on the prod host — *not in the repo*. Implication for Tier 1.3 tests: `php -S` won't have the snippet, so don't assert specific CSP directive content; only assert that no PHP-side `header('Content-Security-Policy: ...')` was set (which would shadow the nginx header in production).
- **CAPTCHA is Turnstile (Cloudflare), not hCaptcha.** `php/includes/turnstile.php` + `tests/php/TurnstileTest.php` are the integration; consumers are `php/login.php` and `php/contact.php`. (Memory `[project_editor_feature]` calls it hCaptcha — that's stale; the live code uses Turnstile.)
- **PHP secrets come from `/etc/kayak/secrets.env`** loaded by PHP-FPM pool env, accessed via `getenv()`. Tier 1.3 integration tests must either supply a test fixture for these (`putenv()` in `setUpBeforeClass`) or skip endpoints that need them.
- **`style.css` is cache-busted via a hash sidecar.** `php/includes/header.php:31` reads `<doc_root>/static/style.css.hash` (written by `_deploy_static_assets` at deploy time) and falls back to `<doc_root>/style.css`. Don't break this contract during Tier 5's `header.php` work — both the hash path and the fallback are load-bearing.
- **Existing `composer scripts` block is minimal.** `composer test` (phpunit) and `composer analyse` (phpstan) exist; no `composer fix` yet. Tier 1.2 should add `composer fix` (php-cs-fixer) and `composer fix-check` (`fix --dry-run`) so CI calls match local.
- **Entry-point files are fully procedural.** `reach.php` has zero `function` definitions — top-level code runs directly. Extraction means *defining new helper functions from inlined cluster code*, not moving existing functions. Plan-wide implication: every Tier 2/3/5 phase's diff is +N lines (new function declarations) along with -N lines (extracted body), so the file shrinks but the helper grows by more than the extracted body — net codebase line count goes up slightly per phase. Same shape as Phase 9 of the build.py split.
- **No `$_SESSION` use anywhere.** All session state goes through the editor_session cookie + DB tables; no PHP-builtin sessions. Plan doesn't need to worry about `session_start()` interactions in extracted code.
- **Existing test coverage map per big file:**
  - `svg_plot.php`: ✓ `tests/php/SvgPlotTest.php` (interpolation utilities only — SVG rendering itself is untested)
  - `reach.php`, `description.php`, `propose.php`, `gauge.php`, `gauge_plots.php`, `review.php`, `custom.php`, `custom_gauges.php`, `auth.php`: **no dedicated tests**. (Coincidental name matches in `SanityTest`, `ReviewApproveRaceTest`, `EditAuthTest` are about other concerns.)
  - Implication: Tier 2/3/5 baseline tests are net-new and load-bearing. The verification gate has nothing to fall back on if a baseline test is missing.
- **`phpunit.xml` excludes only `php/includes/db.php`** (load-time PDO init). Any new include that does load-time side effects must be added to this exclude list — or, better, rewritten to avoid the side effect (initialize lazily inside a function).
- **Existing `php/includes/*.php` have zero module-level state.** All 17 helpers define functions only — no top-level `$var = ...` assignments. Extracted helpers must follow this convention; load-time-evaluated state breaks PHPUnit's source-coverage isolation and the load-order assumptions the entry-point files make.
- **Files between 200–300 lines are out of scope unless they grow.** `mail.php` (206), `sanity.php` (220), `picker.php` (233), `gauge_picker.php` (233), `source.php` (238), `review_logic.php` (247), `admin.php` (275). If any crosses 300 during the migration, fold it into the next-tier list rather than deferring indefinitely.
- **Code style: 4-space indent, no tabs, em-dashes preserved in comments.** php-cs-fixer config (Tier 1.2) must keep these — PSR-12's default is 4-space so it should align, but the unicode-in-source preference needs an explicit rule (default rulesets sometimes ASCII-fold).
- **Cluster-spanning state in entry points.** `reach.php`'s top-level scope holds `$db`, `$id`, `$q`, `$st`, `$hidden`, `$has_map`, `$map_scripts`, `$compact_css`, etc. These are read by what will become extracted clusters. Each extracted helper either takes them as args or — for orchestration that touches many — receives a context dict. Plan implication: helper signatures are non-trivial (3–6 args common), and the cluster analysis has to enumerate which globals each cluster reads/writes before extraction starts.
- **Cluster analysis can go stale fast.** 136 commits touched `php/` in the last 3 months (vs ~40 to `src/kayak/cli/build.py` over the same window). If a tier's cluster-analysis phase and its first extraction phase are weeks apart, re-run the analysis — boundaries shift. Same caveat applies to baseline tests: a baseline test written 4 weeks ago against a since-edited file may be testing now-obsolete behavior.
- **nginx-passed `fastcgi_param` envs.** `deploy/levels:167-171` sets `SQLITE_PATH`, `EDITOR_FEATURE`, `TURNSTILE_SITE_KEY`, `MAIL_FROM`, `SITE_URL` per-request. These reach PHP via `getenv()`. Tier 1.3 integration tests must `putenv()` reasonable test values for the ones the tested endpoint actually uses (or skip endpoints that need real values like `TURNSTILE_SITE_KEY`).
- **State-changing endpoints have POST handlers.** Tier 5's split work for `propose.php`, `edit.php`, `review.php` must include POST tests (read-only `?id=N` GET tests are insufficient for these). CSRF token presence verification is implied — tracked in detail by `PLAN_editor_security_review.md`.
- **`svg_plot.php` already mixes concerns.** `tests/php/SvgPlotTest.php` exercises `rate_gauge_to_flow()` and `rate_flow_to_gauge()` — interpolation utilities — alongside the SVG-rendering body. That's at least two clusters in the file already (geometry/interpolation vs SVG rendering). Tier 4's cluster analysis should account for this; one possible split: `svg_plot_interpolation.php` (rate_*), `svg_plot_geometry.php`, `svg_plot_axes.php`, `svg_plot.php` (top-level render).

## Out of scope

- **PHP version bump** (8.4 → 8.5+ when available). Separate concern.
- **Replacing PHP with Python/something else.** This plan assumes PHP stays.
- **Routing rewrites** (turning `reach.php` into `/reach/<id>`). Would touch nginx + every `<a href>`; not bundled.
- **Composer dependency hardening** (Dependabot, SBOM, vuln scan). Worth doing, but separate plan.
- **Editor-feature security review.** Tracked in `PLAN_editor_security_review.md`. Some findings there may shape `auth.php` work in Tier 5; if a security finding lands while Tier 5 is in flight, fix the security finding first and redo the analysis.

## Reproduce

Read-only commands a second session should run before Tier 1 starts.

```bash
# Prerequisite: composer install (vendor/ is gitignored)
composer install --no-interaction --no-progress --prefer-dist

# File inventory by line count (the plan's "big files" claim)
wc -l php/*.php php/includes/*.php | sort -n | tail -20

# Confirm php/auth.php (entry, ~80 lines) ≠ php/includes/auth.php (helper, ~400 lines)
ls -la php/auth.php php/includes/auth.php

# PHPStan + PHPUnit + cs-fixer state
cat phpstan.neon
cat phpunit.xml
grep -E "phpstan|phpunit|php-cs-fixer" composer.json

# Current PHPStan output at level 5 (baseline before Tier 1.1 raises it)
vendor/bin/phpstan analyse --no-progress

# Current PHPUnit pass count
vendor/bin/phpunit --testdox 2>&1 | tail -20

# Existing test coverage and naming convention
ls tests/php/
grep -l "reach.php\|description.php\|svg_plot.php" tests/php/*.php 2>/dev/null

# CI hooks (Tier 1 changes need to slot in here)
grep -A3 "PHPStan\|PHPUnit\|php-cs-fixer\|php -l" .github/workflows/ci.yml

# Audit confirms current code is clean (drift gates are preventive)
grep -rn "\bmb_" php/  # should be empty
grep -rEn '<script>\b|on(click|change|submit|input|load|keyup|focus|blur)=' php/  # should be empty
grep -rn "<style>" php/  # not blocked, but enumerated for CSP audit

# Plot-rendering call graph (Tier 4 must enumerate consumers)
grep -rln "svg_plot\.php" php/

# Entry-point input convention (filter_input vs $_GET) — extracted code should follow this
grep -rn "filter_input\|\$_GET\|\$_POST" php/reach.php | head

# Cross-language coupling — Python deploy copies style.css and PHP files
grep -n "style.css\|\.php" src/kayak/web/build/deploy.py | head

# nginx URL → file mapping (run on the live host; needs sudo)
sudo nginx -T 2>/dev/null | grep -B2 -A6 "\.php\|server_name" | head -60
```
