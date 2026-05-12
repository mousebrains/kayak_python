# Plan — PHP layer split (apply build.py-split discipline to PHP)

> **Cross-check:** plan drafted 2026-05-11 against the PHP layer at HEAD (`75de842`). A second Claude session should re-run the read-only commands in **§Reproduce** before any tier starts, especially the file-size and PHPStan-level sanity checks — the PHP layer is more actively edited than `cli/build.py` was, and the file inventory may have shifted.

## Why

The PHP layer mirrors the pre-split state of `kayak/cli/build.py`: long monolithic entry-point files (`reach.php` 28KB, `description.php` 21KB, `svg_plot.php` 19KB, six more >14KB), growing user-facing surface, and per-file test coverage near zero outside auth/svg. Editor + propose + review pipelines all run through these files — bugs land in production fast and there's nothing on the lint side preventing complexity from creeping further.

Goal: apply the same per-file-split discipline that just landed for `cli/build.py`. Each big entry-point becomes a thin orchestration file delegating to extracted helpers in `php/includes/`. PHPStan level rises and a formatter joins the gate. Same per-tier review workflow as the build.py and production-discipline plans.

## Constraints

- **Live PHP-FPM lacks mbstring** ([reference_php_no_mbstring]). CI has it; production doesn't. All extracted code must use `strlen`/`substr`/`strtolower` rather than `mb_*`. Easy to forget when extracting; the gate must catch it.
- **CSP enforced; no inline JS or event handlers** ([feedback_csp_no_inline]). Anything moved from inline `<script>` or `onclick=` must land in an external JS file under `static/`.
- **PHP files are entry points, not modules.** Unlike `cli/build.py`, you can't replace `reach.php` with a 5-line shim — nginx routes URLs directly to it via FastCGI. The "split" is extract-helpers-and-include, not reduce-to-shim.
- **`db.php` is excluded from PHPStan source coverage** (per `phpunit.xml`) because it does side-effectful PDO init at load time. Extracted helpers should not replicate that pattern.
- **Tooling already in place.** Composer + PHPStan ^2 (level 5) + PHPUnit ^11.5 + CI runs all three plus `php -l` syntax check. No Tier 0 setup needed; just raise the bar.
- **Phased.** Tier-by-tier review like the build.py split.

## Decisions baked in

- **Tooling-strict first.** Raise PHPStan from level 5 → at least 7 with per-file grandfathering for the unsplit files; add `friendsofphp/php-cs-fixer` as the analog of `ruff format`. These create the gate that the splits then satisfy.
- **Per-file pattern, applied in size order:**
  1. **reach.php** (28KB, no dedicated tests) — biggest payoff, biggest risk; gets the most care.
  2. **description.php** (21KB) — same shape, smaller.
  3. **svg_plot.php** (19KB) — already has `tests/php/SvgPlotTest.php` so the baseline gate is partly in place.
  4. **Remaining >14KB files** (`propose.php`, `gauge.php`, `gauge_plots.php`, `review.php`, `custom.php`, `auth.php`) — not separately planned; the template from Tiers 2–4 gets applied to each in Tier 5.
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
├── reach.php                # ~150 lines: parse args, query DB, render template
├── description.php          # ~150 lines: same shape
├── svg_plot.php             # ~150 lines: same shape
├── propose.php / gauge.php / gauge_plots.php / review.php / custom.php / auth.php
│                            # Tier 5: same template applied
└── includes/
    ├── reach_query.php      # DB queries scoped to reach.php
    ├── reach_render.php     # HTML rendering
    ├── reach_plot.php       # Plot composition (calls into svg_plot)
    ├── description_query.php / description_render.php / ...
    ├── svg_plot_geometry.php / svg_plot_styling.php / svg_plot_render.php
    └── (existing files: auth.php, db.php, header.php, mail.php, ...)
```

Roughly: each big entry-point spawns 2–4 focused includes named `<entrypoint>_<cluster>.php`. The naming makes the call graph obvious from `ls`.

## Migration tiers

Each tier is several phases; **review gate between tiers**, not between phases. Same workflow as the build.py and production-discipline plans.

### Tier 1 — Discipline foundation

**Goal:** Tighten lint + format gates so the splits produce code that meets the new bar.

1. **Phase 1.1 — Raise PHPStan level.** Bump `phpstan.neon` from level 5 → 7 (or 8, if the codebase tolerates it). Add per-file `parameters.ignoreErrors` entries for the unsplit big files as needed; CI must stay green. The grandfathered file list shrinks across Tiers 2–5 as each file is split.
2. **Phase 1.2 — Add `friendsofphp/php-cs-fixer`.** Add to `composer.json` require-dev. Adopt PSR-12 + a ruleset that matches the existing code style. Run `php-cs-fixer fix` to surface the diff; review and apply iteratively until clean. Add `vendor/bin/php-cs-fixer fix --dry-run --diff` to CI as a hard gate (analog of `ruff format --check`).
3. **Phase 1.3 — Baseline integration test scaffold.** New file `tests/php/IntegrationGoldenTest.php` (or directory `tests/php/integration/`). Helpers: `request(string $path, array $query): array` returning `['status' => int, 'body' => string]` via PHP's built-in test server (`php -S` in `setUpBeforeClass`) or via mocking the FastCGI surface. Plus a `golden_response()` helper that asserts response body contains required substrings without requiring byte-identity (HTML order can drift across PHP versions for assoc-array iteration).
4. **Phase 1.4 — Drill.** Add one golden-response test for `reach.php?id=<known_id>`. Confirm it passes; intentionally break a string in `reach.php`; confirm test fails; fix; confirm green.

**Verification gate (end of Tier 1):**
- `composer analyse` (PHPStan) green at the new level
- `composer fix --dry-run` green
- One integration test in CI green and demonstrably catches a real change

### Tier 2 — reach.php split

**Goal:** Reduce `reach.php` from a 28KB monolith to a thin entry point + 2–4 focused includes, no behavior change.

1. **Phase 2.1 — Baseline tests.** Three integration golden-response tests: `reach.php?id=<class-2-reach>`, `?id=<class-5-reach>`, `?id=<reach-with-no-gauge>`. Each asserts: HTTP 200, response includes display name, includes plot div, includes header. These are the gate every subsequent phase must keep green.
2. **Phase 2.2 — Cluster analysis.** Reproduce here, in this doc, the `# Current shape` table from `PLAN_build_split.md` for `reach.php`. Identify ~3–5 clusters (likely: arg parsing + auth check, DB queries, plot rendering, HTML body, header/footer wiring). Note any cross-cluster calls that constrain phase order.
3. **Phase 2.3+ — One phase per cluster.** Extract the cluster to `php/includes/reach_<cluster>.php`. Update `reach.php` to `require_once` and call the extracted functions. Tests + PHPStan + cs-fixer + golden-response must stay green between phases. Trim any imports left behind (PHPStan with strict imports flags them; without it, a manual sweep).
4. **Phase 2.N — Final cleanup.** `reach.php` should be ~150 lines: parse `$_GET`, optional auth, dispatch to extracted helpers, render. Remove from PHPStan grandfather list (Tier 1 added it).

**Verification gate (end of Tier 2):**
- `reach.php` < 200 lines
- `php -l reach.php`, PHPStan, php-cs-fixer all green
- Three baseline golden-response tests still pass
- A side-by-side diff of representative HTML responses (curl the staging vhost pre-tier and post-tier) shows nothing user-visible changed

### Tier 3 — description.php split

Same template as Tier 2, applied to `description.php`. Smaller file, faster.

### Tier 4 — svg_plot.php split

Same template, but: `svg_plot.php` already has `tests/php/SvgPlotTest.php` providing partial baseline coverage. Augment with golden-response tests for representative gauge pages that include the plot. Then extract clusters: SVG geometry math, styling/colors, rendering, axis/tick generation.

### Tier 5 — Apply template to remaining big files

**Goal:** Same discipline applied to `propose.php`, `gauge.php`, `gauge_plots.php`, `review.php`, `custom.php`, `auth.php`. No separate sub-plan — each gets the Tier 2 template.

Per-file phase shape: baseline tests → cluster analysis (in commit messages, not this doc) → extract clusters → cleanup. Order: largest first, but `auth.php` last because it's load-bearing for the editor feature and benefits from any patterns established earlier.

### Tier 6 — PHPStan max + closeout

**Goal:** Final gate sweep.

1. **Phase 6.1 — Empty the grandfather list.** Every file split in Tiers 2–5 should already be off the per-file ignore list. Confirm by deleting the list and running CI. Any file still flagging needs a brief follow-up extraction or a documented justification.
2. **Phase 6.2 — Raise PHPStan to max.** Bump from the Tier-1 level to level 9. Address findings in a follow-up PR if more than 1–2 days of work; otherwise inline the fix.
3. **Phase 6.3 — Update `CLAUDE.md`.** Document the new PHP discipline: PHPStan level, php-cs-fixer command, integration-test pattern. Pointer to `php/includes/` naming convention.

## Risks

- **mbstring trap.** CI has it; production doesn't. Extracted helpers that drift toward `mb_strlen`/`mb_substr` will pass tests but fail in production silently (with subtle character-handling bugs, not crashes). Mitigation: add a CI grep step `! grep -rn "\bmb_" php/` that fails the build.
- **Inline-JS regression.** Splitting render functions might tempt re-introducing inline `<script>` or `onclick=`. CSP will block them in production but tests probably won't catch it. Mitigation: extend the integration golden-response test to assert `! str_contains($body, "<script>")` (or to assert all `<script>` tags have `src=`).
- **Side-effectful loads.** `db.php` initializes PDO at load time. Any new include that does similar work at load (rather than via a function) breaks the test isolation pattern. Mitigation: PHPStan rule, or convention enforced by code review.
- **Endpoint behavior drift.** Unlike `cli/build.py` (output is HTML files), PHP entry points have HTTP request semantics: query params, cookies, sessions, headers. A subtle change in request-parsing order can change `$_GET` precedence over `$_POST` — easy to miss. Mitigation: golden-response tests with multiple query patterns.
- **Test flakiness from `php -S`.** Built-in test server can race with port reuse on quick CI re-runs. Mitigation: bind to port 0 and read the assigned port; or use a unix socket.
- **Editor feature is load-bearing.** Splitting `auth.php` or `propose.php` while real users are editing risks user-visible failures. Tier 5 ordering puts these last; consider deploying to `levels-test.wkcc.org` first and waiting a week before promoting.

## Out of scope

- **PHP version bump** (8.4 → 8.5+ when available). Separate concern.
- **Replacing PHP with Python/something else.** This plan assumes PHP stays.
- **Routing rewrites** (turning `reach.php` into `/reach/<id>`). Would touch nginx + every `<a href>`; not bundled.
- **Composer dependency hardening** (Dependabot, SBOM, vuln scan). Worth doing, but separate plan.
- **Editor-feature security review.** Tracked in `PLAN_editor_security_review.md`. Some findings there may shape `auth.php` work in Tier 5; if a security finding lands while Tier 5 is in flight, fix the security finding first and redo the analysis.

## Reproduce

Read-only commands a second session should run before Tier 1 starts.

```bash
# File inventory and sizes (the plan's "big files" claim)
find php/ -name "*.php" -type f -printf '%s %p\n' | sort -nr | head -25

# PHPStan + PHPUnit + cs-fixer state
cat phpstan.neon
cat phpunit.xml
grep -E "phpstan|phpunit|php-cs-fixer" composer.json

# Existing test coverage
ls tests/php/
grep -l "reach.php\|description.php\|svg_plot.php" tests/php/*.php

# CI hooks (Tier 1 changes need to slot in here)
grep -A3 "PHPStan\|PHPUnit\|php-cs-fixer\|php -l" .github/workflows/ci.yml

# mbstring and inline-script audit (Risks section)
grep -rn "\bmb_" php/  # should be empty if production-safe today
grep -rn "<script>\|onclick=" php/  # inline-script audit
```
