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
2. **Phase 1.2 — Add `friendsofphp/php-cs-fixer`.** Add to `composer.json` require-dev. Adopt PSR-12 + a ruleset that matches the existing code style. Run `php-cs-fixer fix` to surface the diff; review and apply iteratively until clean. Add `vendor/bin/php-cs-fixer fix --dry-run --diff` to CI as a hard gate (analog of `ruff format --check`).
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

1. **Phase 2.1 — Baseline tests.** Four integration golden-response tests covering the three `reach.php` modes plus the no-gauge edge: `?q=<search-term>` (search mode), `?` (list mode, no params), `?id=<class-2-reach>` (detail mode), `?id=<reach-with-no-gauge>` (detail edge). Each asserts: HTTP 200, response includes mode-appropriate substring, CSP header present (set by nginx; integration tests via `php -S` won't have it but should assert no PHP `header('Content-Security-Policy: ...')` was set). These are the gate every subsequent phase must keep green.
2. **Phase 2.2 — Cluster analysis.** Reproduce here, in this doc, the `# Current shape` table from `PLAN_build_split.md` for `reach.php`. The file already has three obvious top-level branches (search / list / detail — all calling `include_header` + `include_footer`); these are natural cluster boundaries. Beyond mode-dispatch, expect: arg parsing, DB queries, HTML rendering. Note `reach.php` only includes 4 helpers (db, header, footer, html) — it's leaner than `description.php` (8 includes) so the cluster count is likely 3–4, not 5+.
3. **Phase 2.3+ — One phase per cluster.** Extract the cluster to `php/includes/reach_<cluster>.php`. Update `reach.php` to `require_once` and call the extracted functions. Tests + PHPStan + cs-fixer + golden-response must stay green between phases. Trim any imports left behind (PHPStan with strict imports flags them; without it, a manual sweep).
4. **Phase 2.N — Final cleanup.** `reach.php` should be ~150 lines: parse `$_GET`, optional auth, dispatch to extracted helpers, render. Remove from PHPStan grandfather list (Tier 1 added it).

**Verification gate (end of Tier 2):**
- `reach.php` < 200 lines
- `php -l reach.php`, PHPStan, php-cs-fixer all green
- Three baseline golden-response tests still pass
- A side-by-side diff of representative HTML responses (curl the staging vhost pre-tier and post-tier) shows nothing user-visible changed

### Tier 3 — description.php split

Same template as Tier 2, applied to `description.php`. Smaller file (495 lines vs reach.php's 649) but **denser cross-cluster dependencies**: 8 includes (db, header, footer, html, svg_plot, gauge_plots, gauge_map, validate) vs reach.php's 4. Single-mode entry point (`?id=N` only — 400 on missing); baseline tests are 2 (a class-2-with-gauge reach detail; a no-gauge reach detail) plus the 400 edge. Date filtering (`start`, `end`, `hidden` params) widens the parameter space; baseline tests should hit a date-windowed and an unwindowed call.

Reuses the existing `get_reach_or_404($id)` from `php/includes/db.php:50` for 404 handling — extracted helpers should follow this naming convention for any new fail-fast lookups.

### Tier 4 — `php/includes/svg_plot.php` split

`svg_plot.php` is an **include**, not an entry point — `description.php`, `plot.php`, `php/includes/gauge_plots.php`, and (transitively via gauge_plots) `gauge.php` all `require_once` it. The "split" here is sub-helper extraction, not orchestration extraction. The 503-line file becomes 2–4 smaller includes under `php/includes/`.

`tests/php/SvgPlotTest.php` is the existing baseline. Augment with golden-response tests on the *consumer* entry points (`description.php?id=...`, `plot.php?...`, `gauge.php?...`) — these catch behavior drift in the consumers, not the helper directly.

Likely cluster split: SVG geometry math (`svg_plot_geometry.php`), styling/colors (`svg_plot_styling.php`), axis/tick generation (`svg_plot_axes.php`), top-level render that assembles them (the surviving `svg_plot.php`, ~150 lines).

### Tier 5 — Apply template to remaining big files

**Goal:** Same discipline applied to the remaining six big files, in two shapes:

**Entry-point template** (orchestration extraction, like Tiers 2/3): `propose.php` (430), `gauge.php` (432), `custom.php` (363), `custom_gauges.php` (325), `review.php` (318). Order: largest first. `gauge.php` follows the reach.php multi-mode pattern (`?id=` detail vs `?q=` search); baseline tests must cover both. `propose.php`, `edit.php`, `review.php` have POST handlers — baseline tests must include POST cases with valid CSRF tokens.

**Helper-split template** (sub-helper extraction, like Tier 4): `php/includes/auth.php` (393), `php/includes/gauge_plots.php` (386). For each: identify subdomains (auth.php splits into session, magic-link, csrf, throttle clusters; gauge_plots.php splits into multi-series-rendering, axis-handling, etc.). The existing convention (snake_case functions, no namespace, no class) carries forward.

Per-file phase shape: baseline tests → cluster analysis (in commit messages, not this doc) → extract clusters → cleanup. Order: entry-points first (build experience with the template); then `gauge_plots.php`; then `auth.php` last because it's load-bearing for the editor feature and benefits from any patterns established earlier.

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
