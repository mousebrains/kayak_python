# Plan — JS smoke tests via Playwright in CI

> **Cross-check:** plan drafted 2026-05-12 against `main` at `d3e7dce`; iter 1 re-verified against `8ad4a37` (the initial-draft commit). Inputs verified: `tests/php/IntegrationTestCase.php` (Tier 1.3 PHP scaffold; `resolveVenvCommand:204`), `.github/workflows/ci.yml` (`lint-misc:27`; `pip install -e .:70-71`), `pyproject.toml` (editable install pattern), `static/*.js` + `src/kayak/web/static/*.js` (10 hand-written JS files, all IIFE-style classic scripts).
>
> **Iter log:**
> - iter 1 (2026-05-12): 5 findings — Playwright's `webServer` block can't read env vars set in `globalSetup` (timing race); replaced with manual server lifecycle inside `globalSetup` mirroring `IntegrationTestCase.php`. `levels init-db` needs both `SQLITE_PATH` AND `DATABASE_URL` env vars, not just one. Phase 2 runtime budget phrasing clarified (≤30 s is total step time, not delta). Added `actions/upload-artifact` for `playwright-report/` on CI failure. Cross-check ref bumped + line-citations added.
> - iter 2 (2026-05-12): 4 findings — Phase 1's `php -S -t public_html` serves the repo's tracked PHP symlinks, but Phase 3's `/Oregon.html` test needs `levels build` output that's not in the repo. Made the build step explicit in Phase 3's globalSetup expansion. Added `OUTPUT_DIR=<tmpdir>` isolation to globalSetup so the test build never clobbers the dev box's actual build output (whether default or per `PLAN_dev_env_followups.md` Phase 3 convention). Phase 3 risks: added Playwright `navigationTimeout` note re Leaflet tile-load timing.
> - iter 3 (2026-05-12, this revision): 5 findings — `/custom.php` and `/custom_gauges.php` return HTTP 302 when called without `?ids=` (early redirect at `custom.php:19`); a smoke test asserting 200 would fail. Dropped from Phase 3's coverage; either page would need seeded reach IDs to exercise. Made "smoke = page-load only, no interactions" explicit in Phase 3 scope (resolves the gauge_picker XHR open question). Documented `--with-deps` no-sudo fallback (Phase 1 verification gate on no-sudo dev hosts like `levels` uses plain `npx playwright install chromium`). Confirmed `Oregon` is a seeded state per `init_db.py:29`. Cross-check ref bumped to `d3d6857`.
>
> Dates absolute. References `file:line` against current `main`.

## Context

The repo has 10 hand-written JS files (`static/sw.js`, `static/map.js`, `static/picker.js`, `static/reach-map.js`, `static/search-map.js`, `static/feature-map.js`, `static/gauge_picker.js`, `static/plot-hover.js`, `src/kayak/web/static/levels.js`, `src/kayak/web/static/filters.js`) plus the vendored `static/leaflet.js`. Today's CI surface for JS is `biome check` only — a linter, not a runtime check. `PLAN_js_cleanup.md` Phase 3 (the `var → const/let` modernization) caught regressions via manual browser walk-throughs, which worked but doesn't scale: any future refactor (`PLAN_php_layer_split.md` Tier 2-5 entry-point splits, future Leaflet upgrades, new JS additions) needs the same human gate.

A Playwright-based headless-browser smoke test in CI catches the regression classes the JS cleanup discipline cared about — strict-mode `ReferenceError`, closure-over-loop-var bugs, Leaflet wiring breaks, page-level JS crashes — without forcing the codebase out of its classic-script (`<script src="…" defer>`) convention. The existing PHP integration scaffold (`tests/php/IntegrationTestCase.php`, landed in commit `d3e7dce`) is the architectural precedent: a real `php -S` server on an OS-assigned port, with a `levels init-db`-seeded SQLite DB, driven by tests in CI. Playwright extends this naturally — same server-spawn pattern, same DB seeding, but the client is a headless Chromium instead of `proc_open`'d curl.

This plan adds Playwright in three phases — bootstrap locally, wire CI, then expand coverage — so each commit is independently reviewable and revertable.

## Why

The JS regression net is the weakest tier-gate in CI today:
- **Python:** ruff lint + ruff format + mypy + pytest (624 tests, ≥75 % coverage). Strong.
- **PHP:** php -l + PHPStan level 7 + php-cs-fixer + PHPUnit (56 tests, including 1 integration). Strong.
- **Shell:** shellcheck. Adequate for the ~20 scripts.
- **CSS:** biome. Adequate.
- **JS:** biome lint only. **No runtime check.**

The shape of the JS bugs the project has actually hit:
1. The `PLAN_js_cleanup.md` Phase 3a refactor of `map.js:276-299` (the 5 sequential `for(var i)` loops sharing a hoisted `i`) would have been silently broken by biome's mechanical `var → let` autofix. The plan caught it via a manual pre-fix audit; a smoke test that hits `/map.html` and filters to 0 reaches then back would have failed loudly post-refactor without that audit.
2. Phase 3c (`search-map.js:5-7`) — the `var reaches, colors` declared in a `try` but used outside it. Strict-mode + `let` would `ReferenceError` if the audit missed it. A smoke test loading any page with `<div id="search-map">` would catch it.
3. The `php/includes/header.php` PHP-side `Referrer-Policy: no-referrer` override (F-14 in `docs/security/findings.md`) is silently neutralized by the duplicate `Referrer-Policy` from nginx's snippet — a smoke test asserting `response.headers['referrer-policy']` on `/auth.php` would have surfaced this within seconds of the original commit.

Goal: a single CI job step that spawns `php -S` against a seeded test DB, loads ~7 key page-types in a headless browser, and asserts each loads with zero console errors and zero `pageerror` events. Add detail-view tests later if appetite.

## Current state (verified)

| Item | State |
|---|---|
| Node toolchain on this host | absent (`which node` → empty) |
| `package.json` / `package-lock.json` | absent (no Node manifest in repo) |
| `tests/js/` directory | absent |
| `.github/workflows/ci.yml` `lint-misc` job | already has PHP 8.4 + Python 3.13 + `pip install -e .` + composer install (lines 27-90) |
| `tests/php/IntegrationTestCase.php` | reference pattern for port-0 server boot + tmp-DB seeding (landed `d3e7dce`) |
| Hand-written JS files | 10 total: see §Context |
| biome lint enforcement | covers all 10 (per `PLAN_js_cleanup.md` Phase 1 closeout) |
| Existing PHP integration test | 1 (`ReachIntegrationTest::testStateFilterListRenders` — verifies `/reach.php?st=OR`) |

Pages worth smoke-testing (JS file mapping; init-db'd test DB has zero observations so detail views may 404):
- `/index.html` or per-state HTML (e.g. `/Oregon.html`) — `levels.js`, `filters.js`, `plot-hover.js`
- `/reach.php?st=OR` — same JS, list view of zero reaches
- `/picker.php` — `picker.js`, `filters.js`, `search-map.js`
- `/gauge_picker.php` — `gauge_picker.js`, `filters.js`
- `/custom.php` — `filters.js`
- `/custom_gauges.php` — `filters.js`
- `/map.html` — `map.js` (the largest JS file, biggest blast radius)
- `/description.php?id=<seeded>` — `feature-map.js`, `reach-map.js`, `plot-hover.js` (needs DB row)
- `/gauge.php?id=<seeded>` — `feature-map.js`, `plot-hover.js` (needs DB row)

Service worker (`sw.js`) intentionally out of scope — full behavior requires HTTPS context + a real registration lifecycle that headless Chromium can do but adds setup complexity disproportionate to the regression risk (sw.js is 44 LOC, all event handlers, low change frequency).

## Phase 1 — Local scaffold (1 commit, ~30 minutes)

Goal: working Playwright harness on a dev box. CI integration deferred to Phase 2 so this phase's verification gate is local-only.

**Files to add:**

1. **`package.json`** — npm manifest with `@playwright/test` as a devDependency. Minimal scripts:
   ```json
   {
     "name": "kayak-js-tests",
     "private": true,
     "scripts": {
       "test": "playwright test",
       "test-install-browsers": "playwright install chromium"
     },
     "devDependencies": {
       "@playwright/test": "^1.51.0"
     }
   }
   ```
   Pin minor for stability; Playwright follows semver rigorously. Newest stable as of plan draft is 1.51.x (March 2026 release).
2. **`package-lock.json`** — committed for reproducible installs (mirrors `composer.lock` discipline in the PHP layer).
3. **`playwright.config.ts`** — Playwright config:
   - `testDir: 'tests/js'`
   - `workers: 1` (`php -S` is single-threaded; serial execution avoids races)
   - `globalSetup: './tests/js/global-setup.ts'` (init-db's a tmp SQLite **and** spawns + waits for the PHP server)
   - `globalTeardown: './tests/js/global-teardown.ts'` (stops the PHP server + cleans the tmp dir)
   - `use: { viewport: { width: 1280, height: 720 }, baseURL: process.env.KAYAK_TEST_BASE_URL }` — `headless: true` is the default in CI; explicit only if needed
   - `reporter: [['list'], ['html', { open: 'never' }]]`
   - Browser binary: `chromium` only (cheapest; covers the vast majority of real-user traffic; Firefox + WebKit deferred)
   - **No `webServer` block.** Playwright's `webServer` config launches the command before any test code runs — *before* `globalSetup` exports env vars. That means the spawned `php -S` can't see the `SQLITE_PATH` written by `globalSetup`. The clean answer (mirroring `tests/php/IntegrationTestCase.php`'s pattern) is to own the server lifecycle inside `globalSetup`/`globalTeardown` and export `process.env.KAYAK_TEST_BASE_URL` for the tests to consume via `baseURL`.
4. **`tests/js/global-setup.ts`** — analog of `IntegrationTestCase::setUpBeforeClass`:
   - Mints a tmp dir (e.g. `mkdtempSync(path.join(os.tmpdir(), 'kayak-js-'))`).
   - Runs `levels init-db` via `child_process.execFileSync` with **both** env vars set:
     ```ts
     execFileSync('levels', ['init-db'], {
       env: {
         ...process.env,
         SQLITE_PATH: dbPath,                            // read by PHP layer
         DATABASE_URL: `sqlite:///${dbPath}`,           // read by Python layer
         EDITOR_FEATURE: '0',
       },
       stdio: 'pipe',
     });
     ```
     Both env vars are required — `kayak.config` reads `DATABASE_URL` for SQLAlchemy; PHP reads `SQLITE_PATH` from `fastcgi_param` (here from the process env). One without the other → schema seeded only partially OR the PHP server can't open the DB.
   - Spawns `php -S 127.0.0.1:<port> -t public_html` via `spawn()`, with `SQLITE_PATH` + `EDITOR_FEATURE` etc. in its env. Uses a fixed port (e.g. `8000`) — Playwright can't probe a port-0 binding because there's no PHPUnit-style stderr-reading layer in the runner; fixed-port on isolated CI runners is reliable. If 8000 is occupied on a dev box, override via `KAYAK_TEST_PORT` env var.
   - For Phase 1: serves directly from the repo's `public_html/` (the 24 tracked `120000`-mode symlinks resolve to `php/*.php`; CI clones include the symlinks). No `levels build` needed yet — the drill test hits `/reach.php?st=OR` which is a tracked symlink. Phase 3 changes this (see below).
   - Polls the port (`net.createConnection`) for up to 5 s until it accepts.
   - Stores `{ serverPid, tmpDir, dbPath }` on `process.env.KAYAK_TEST_*` for the teardown to find. Sets `process.env.KAYAK_TEST_BASE_URL = http://127.0.0.1:8000` for the tests to read via `baseURL`.
5. **`tests/js/global-teardown.ts`** — stops the PHP server (`process.kill(serverPid, 'SIGTERM')` with a short timeout then `SIGKILL` fallback) + `rm -rf tmpDir`. Always runs on clean exit; crashed runs leak the server process + tmpdir on purpose so the operator can inspect.
6. **`tests/js/smoke.spec.ts`** — single drill test:
   ```typescript
   import { test, expect } from '@playwright/test';

   test('reach.php?st=OR loads with no JS errors', async ({ page }) => {
     const errors: string[] = [];
     page.on('pageerror', err => errors.push(err.message));
     page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()); });

     const resp = await page.goto('/reach.php?st=OR');
     expect(resp?.status()).toBe(200);
     await expect(page.locator('body')).toContainText('reaches matching');
     expect(errors).toEqual([]);
   });
   ```
   Same invariant as `ReachIntegrationTest::testStateFilterListRenders` plus the JS-error assertion. Validates the harness end-to-end on the simplest page.
7. **`.gitignore`** — add:
   ```
   node_modules/
   playwright-report/
   test-results/
   /tests/js/.cache/
   ```

**Verification gate:**
- Dev box (with sudo): `npm ci && npx playwright install --with-deps chromium && npx playwright test` runs the spec and exits 0.
- Dev box without sudo (e.g., the live host where `[feedback_no_sudo]` applies): substitute `npx playwright install chromium` (no `--with-deps`); relies on system Chromium libraries already being present. If a missing-lib error surfaces at first browser launch, the system needs `libgbm1 libnss3 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgtk-3-0 libatspi2.0-0` — install via the package manager off-process. CI runners include these by default.
- `tests/js/smoke.spec.ts` is the only test; output reads `1 passed`.
- The drill: change `'reaches matching'` to `'reaches NO_SUCH_STRING'` in the spec, re-run, confirm the test fails with a clear diff. Restore, re-run, confirm green again (mirrors the PHP Tier 1.4 drill protocol in commit `d3e7dce`).
- No commit yet to `.github/workflows/ci.yml` — CI's biome lint gate covers no Playwright surface yet; this phase is sandbox-only.

**Risk:**
- `npm ci` fetches packages from npmjs.org without integrity checks beyond the lock file. Same trust model as `composer install`. Mitigation: lock file pinned; `npm audit` runs naturally as a separate signal.
- `levels init-db` requires `/home/pat/.venv/bin/levels` (live host) or `${VIRTUAL_ENV}/bin/levels` (dev box) to be on PATH. The global-setup must locate it — same logic as `IntegrationTestCase::resolveVenvCommand` (`tests/php/IntegrationTestCase.php:204`); port the algorithm.
- Headless Chromium's HTTP/2 stack rejects self-signed certs; the spec uses HTTP (`http://127.0.0.1:<port>`) not HTTPS, so unaffected.

## Phase 2 — CI integration (1 commit, ~15 minutes)

Goal: CI gate. The plan deliberately doesn't expand test coverage yet — get one test running green in CI first, then add tests in Phase 3 with confidence the harness works.

**Files to modify:**

1. **`.github/workflows/ci.yml` — `lint-misc` job:** add Node + Playwright steps after the existing `pip install -e .` step (which currently lives at lines 70-71). The PHP server and the test DB seeding already require PHP 8.4 + `levels` CLI; both are present in `lint-misc`. Snippet to add:
   ```yaml
   - uses: actions/setup-node@v4
     with:
       node-version: '22'  # current LTS as of 2026-05; Node 20 EOL April 2026
       cache: 'npm'
   - name: Install JS test deps
     run: npm ci
   - name: Cache Playwright browsers
     uses: actions/cache@v4
     with:
       path: ~/.cache/ms-playwright
       key: ${{ runner.os }}-playwright-${{ hashFiles('package-lock.json') }}
   - name: Install Playwright browser (chromium)
     run: npx playwright install --with-deps chromium
   - name: JS smoke tests
     run: npx playwright test
   - name: Upload Playwright report (on failure)
     if: failure()
     uses: actions/upload-artifact@v4
     with:
       name: playwright-report
       path: playwright-report/
       retention-days: 14
   ```
   Order matters: Node + npm ci must precede playwright install (needs `@playwright/test` on disk); playwright install must precede the test run.
2. **No other workflow changes needed.** The other jobs (`lint`, `typecheck`, `test`, `secret-scan`, `security-audit`) don't touch JS.

**Verification gate:**
- Push the commit to a branch; CI runs the new step.
- First run cost: ~60-90 s (Node setup ~5 s + `npm ci` ~10 s + `playwright install --with-deps` ~45 s + tests ~10 s).
- Cached runs (npm cache hit + Playwright browser cache hit): ~25-35 s total for the added steps.
- Job stays green; the new `lint-misc` total runtime ≤ ~2 min 30 s (existing ~2 min + ~30 s cached delta). Below the 5-min envelope the workflow targets.
- Cache hit on the second run: confirm `actions/cache@v4` reports `cache-hit: true` for the playwright key in the action log.
- On test failure (intentional or not), the `playwright-report/` directory is uploaded as a workflow artifact for inspection without re-running.

**Risk:**
- Playwright browser binary ~150 MB. GitHub Actions cache limit is 10 GB total per repo; one cached browser fits comfortably.
- `--with-deps` runs `apt-get install` for Chromium system libs; uses sudo on the runner (GitHub-hosted Ubuntu runners support sudo). On the prod host (which can't sudo per `[feedback_no_sudo]`), use `npx playwright install chromium` without `--with-deps` and rely on already-installed system libs — same fallback the official docs describe.
- The `cache: 'npm'` directive in setup-node caches `~/.npm` (npm's package store), accelerating `npm ci` to ~5-10 s. The Playwright browser cache is a separate `actions/cache@v4` step.
- `npm ci` requires `package-lock.json`; if a contributor modifies `package.json` without running `npm install` locally and committing the updated lock file, `npm ci` errors out — same discipline as `composer.lock`.

## Phase 3 — Expand coverage (1 commit, ~30 minutes)

Goal: cover the remaining JS-heavy pages with smoke tests. Each test follows the same shape as Phase 1's drill — `page.goto`, expect `status === 200`, expect no `pageerror`/console-errors, optionally assert a key DOM element renders.

**Scope: page-load only. No user interactions.** Smoke tests assert that pages render and JS initializes without throwing. Tests do NOT click filter pills, drag the map, hover sparklines, or submit forms. Interaction-coverage is a separate plan (and would require a different toolchain investment — likely Playwright fixtures + DOM-state assertions per scenario). This scoping keeps the gate fast, robust against flakes, and aligned with the regression class the JS cleanup work actually cared about (page-load crashes from strict-mode / closure-over-loop-var bugs).

**Setup expansion (one extra step in `global-setup.ts` before the server spawn):** the per-state HTML test (`/Oregon.html`) needs build output; the static-build paths aren't tracked. Add a `levels build` invocation that targets a tmp dir so the test never clobbers the dev box's actual build output:

```ts
const buildOutput = path.join(tmpDir, 'public_html');
execFileSync('levels', ['build'], {
  env: {
    ...process.env,
    SQLITE_PATH: dbPath,
    DATABASE_URL: `sqlite:///${dbPath}`,
    OUTPUT_DIR: buildOutput,    // isolates from dev box's normal target
    EDITOR_FEATURE: '0',
  },
  stdio: 'pipe',
});
```

Then switch the `php -S` invocation from `-t public_html` to `-t ${buildOutput}`. The build copies tracked PHP files + builds state HTML + writes `static/style-<hash>.css` etc. into `$buildOutput`, giving the tests a self-contained docroot that mirrors production layout. Cost: `levels build` runs in ~6 s on hardware comparable to GitHub runners — acceptable startup cost.

This OUTPUT_DIR isolation aligns with `PLAN_dev_env_followups.md` Phase 3's convention (the dev box sets `OUTPUT_DIR` outside the repo); the test does the same thing for a tmp dir.

**Tests to add to `tests/js/smoke.spec.ts`:**

| Test | URL | JS exercised | Assertion notes |
|---|---|---|---|
| State HTML page renders | `/Oregon.html` (one of the per-state files emitted by `levels build`) | `levels.js`, `filters.js`, `plot-hover.js` | Renders the levels-table sticky header; filter pills are clickable. Needs `levels build` in global-setup (current global-setup only runs init-db). |
| Map page renders | `/map.html` | `map.js` (Leaflet) | Map div has Leaflet `_leaflet_id` after init; layer-control rendered; status filter buttons exist. The 5-loop `_mfCasing/_mfHit` rendering loop (`map.js:276-299`) is exercised when the map JSON data loads, even with an empty `reaches-geom.json`. |
| Picker renders | `/picker.php` | `picker.js`, `filters.js`, `search-map.js` | Pillbar renders; `search-map` div hidden until a state pill is toggled (no interaction in smoke). Tests only the initial-load JS execution. |
| Gauge picker renders | `/gauge_picker.php` | `gauge_picker.js`, `filters.js` | Pillbar renders. State-pill click would trigger an XHR to fetch gauge list — but smoke tests don't click, so the XHR is never triggered. Initial-load coverage only. |

`/custom.php` and `/custom_gauges.php` are excluded from this phase: both return **HTTP 302** when called without `?ids=` (early redirect at `custom.php:19`). A meaningful smoke test against either would require seeded reach/gauge IDs and the `?ids=N,M,P` query — out of scope for "no seeded data" Phase 3.

The detail-view pages (`/description.php?id=<n>`, `/gauge.php?id=<n>`) need DB rows. Two options:
1. **Skip detail views** in Phase 3; track separately as Phase 4 if appetite emerges. Lightest.
2. **Seed minimal data** in global-setup: insert one reach + one gauge + one source after `levels init-db`. Adds ~10 lines to global-setup; lets the detail tests exercise `feature-map.js` and `plot-hover.js` paths that aren't reached by list views.

**Recommend option 1** for Phase 3: list-view coverage already exercises 7 of the 10 JS files. The `feature-map.js` and `reach-map.js` paths missed by list-only coverage are the same Leaflet shapes `map.js` covers; the marginal value of detail-view tests is low against the schema-stability + maintenance cost.

**Verification gate:**
- All tests in `smoke.spec.ts` pass locally (`npx playwright test`).
- CI green (Phase 2's job runs the expanded spec).
- Total test time stays under 60 s on cached runs (each Playwright page-goto is ~500-800 ms; 6-7 tests ≈ 5 s sequential).
- One drill per added test (mirroring Phase 1's pattern): break the assertion, confirm clear failure, restore, confirm green. Document the drill in the commit message.

**Risk:**
- Per-state HTML page (`/Oregon.html`) requires `levels build` to have run in global-setup — Phase 3's setup-expansion step (above) adds it. Build runs in ~6 s; acceptable startup cost. Mitigation if it ever grows: cache the built output across CI runs by hashing the inputs (`pyproject.toml` + `data/sources.yaml` + `src/kayak/web/build/**`).
- **Playwright navigation timeout vs Leaflet tile fetches.** Default `navigationTimeout` is 30 s. `page.goto` resolves on the `'load'` event, which fires *before* Leaflet's tile layers finish fetching from OpenTopoMap/OSM. Tests must NOT `await page.waitForLoadState('networkidle')` — that path waits for tiles and will flake against slow upstream CDNs. The drill spec's `goto` + DOM assertions are tile-independent. Document this constraint as a comment at the top of `smoke.spec.ts` so future contributors don't add tile-blocking waits.
- `gauge_picker.php` XHR call may fail noisily against an empty DB. Option: use `page.route()` to stub the response, or seed a minimal gauge. Tracking as a constraint, not a blocker.
- Per-test `page.on('pageerror')` listener attached at test start; assertions run after `page.goto` awaits load. Race-window concern: an error fired during pre-load could be missed. Playwright's `page.goto` is synchronous-await; the listener attaches before navigation. Safe.

## Decisions to make

1. **Node version: 22 (LTS) vs 24 (Current).** Node 20 enters maintenance LTS April 2026 (now). Node 22 became Active LTS Oct 2024 and runs until Apr 2027. Node 24 was released Apr 2026 and won't become LTS until Oct 2026. The plan picks 22 for the predictable LTS window; switching to 24 means moving with the current major release every 6 months. **Recommend 22.**
2. **Browser scope: chromium only, vs all three (chromium + firefox + webkit).** Each browser binary is ~150 MB and adds ~10 s per page on first run. Chromium covers the dominant share of real-user traffic and catches the same JS-runtime regressions the plan aims at. Firefox/WebKit-specific bugs (rare for this codebase) would surface in browser smoke-testing by the operator. **Recommend chromium-only.**
3. **TypeScript vs plain JS for the test files.** `@playwright/test` includes its own TS transpilation; no extra config needed. The PHPUnit suite uses TypeScript-equivalent strictness (typed function signatures); the Python tests do the same via mypy. Plain JS tests would buck the project-wide typed-tests pattern. **Recommend TypeScript** (cost: zero, since Playwright handles the transpilation).
4. **Test location: `tests/js/`** mirrors `tests/php/`. Alternatives (`tests/playwright/`, `playwright-tests/`) are less symmetric with the rest of the test inventory. **Recommend `tests/js/`.**
5. **Detail-view test coverage now vs later.** See Phase 3 Risk; recommendation is to **defer** until appetite emerges; if a real bug ever escapes the list-view tests in production, expand then.

## Risks

- **Playwright maintenance cost.** Each major version bump (annual) may require a minor `playwright.config.ts` shift. The maintenance shape is comparable to `composer.lock` + `package-lock.json` upkeep — a few minutes per quarter once Dependabot or manual runs identify a pending bump. Acceptable.
- **CI runtime budget.** First-run cost is ~60 s for Playwright install + ~5 s for tests; cached runs are ~10 s install + ~5 s tests. Total `lint-misc` runtime delta should stay under 30 s amortized. Watch for creep in Phase 3 if test count grows beyond ~10.
- **`php -S` quirks.** PHP's built-in server is single-threaded and doesn't honor every nginx-specific directive (rewrite rules, fastcgi_params). The test DB's PHP runs without any of nginx's security-headers / rate-limit / fastcgi_param scaffolding. Tests must not rely on headers nginx adds (`Content-Security-Policy`, `Strict-Transport-Security`); those belong in the production smoke-test layer, not the JS-CI scaffold. The existing `ReachIntegrationTest::testStateFilterListRenders` (commit `d3e7dce`) explicitly asserts `assertArrayNotHasKey('content-security-policy', $resp['headers'])` for the same reason; Phase 1's spec follows that precedent.
- **Test flakes from CDN-dependent assets.** `static/leaflet.js` is vendored locally; OpenTopoMap / OpenStreetMap / Esri tile servers are referenced from `map.js` and `feature-map.js`. If a test ever does a network-dependent assert (e.g., wait for a tile to load), CI runs hitting the upstream CDN would flake when those services are slow. Mitigation: smoke tests assert only "no JS error" / "key DOM element exists" — never wait for tile loads. `page.goto` waits for `'load'` event, which fires before tiles finish. Documented constraint in `playwright.config.ts` comments.
- **Local-host networking quirks.** Playwright spawns Chromium in a sandbox; on the live host (rare invocation; CI is the primary execution context), `--no-sandbox` may be needed. Match Playwright docs' guidance; not a CI concern but a doc-the-workaround.
- **Coverage gaps for service worker (`sw.js`).** Out of scope per §Context. If a future PR breaks `sw.js`, the smoke tests won't catch it. Acceptable: sw.js is 44 LOC, changes rarely, and the regression class (network-first cache fallback) requires HTTPS + reload-while-offline to manifest — heavy test setup for low value.

## Out of scope

- **Visual regression / snapshot testing** (Percy, Chromatic, Playwright's `toHaveScreenshot()`). Adds storage + diff-review overhead; the plan's "no JS error" gate catches the regression classes that matter without pixel-level discipline.
- **Cross-browser coverage** (Firefox, WebKit). See Decisions §2.
- **End-to-end editor flows** (login, propose, comment, review). Requires Turnstile bypass + email-mock + maintainer-fixture infrastructure; non-trivial. Defer until editor pipeline gets its own test plan.
- **Performance budgets.** No Lighthouse / page-weight gates. The site's static-build output is small (~84 files, mostly HTML); performance regressions surface in operator review.
- **Mocking external APIs** (USGS, NOAA, etc.). The test DB has no observations; pages render in "no data" state. Mocking external feeds is the data-pipeline's responsibility, not the JS smoke tier's.
- **JS unit tests (Vitest).** Separate decision; see CLAUDE earlier conversation 2026-05-12. The codebase's IIFE-with-no-exports shape blocks Vitest without a module refactor.
- **Reusing the existing PHP integration scaffold.** Considered, rejected. `IntegrationTestCase.php` boots `php -S` via `proc_open` — Playwright's `webServer` block does the same thing with first-class lifecycle handling. Bridging them would be more code than just letting Playwright own its own server lifecycle.

## Reproduce

Read-only commands to verify the plan's inputs before Phase 1.

```bash
# Inputs that should exist
test -f tests/php/IntegrationTestCase.php && echo "  PHP scaffold present ✓"
test -f .github/workflows/ci.yml && echo "  CI workflow present ✓"
test -f pyproject.toml && echo "  Python package present ✓"

# Things that should NOT yet exist
test ! -f package.json && echo "  package.json absent (expected) ✓"
test ! -d tests/js && echo "  tests/js/ absent (expected) ✓"

# Confirm levels CLI is locatable for global-setup
/home/pat/.venv/bin/levels --help | head -2

# Confirm 10 hand-written JS files (count should be 10; static/leaflet.js excluded)
find static src/kayak/web/static -maxdepth 1 -name '*.js' \
  -not -name 'leaflet.js' | wc -l

# CI workflow lint-misc job already has the prerequisites
grep -nE 'setup-python|composer install|pip install -e' .github/workflows/ci.yml

# What playwright version is current?
curl -s https://api.github.com/repos/microsoft/playwright/releases/latest \
  | grep '"tag_name"'
```

Expected output: `10` for the JS count; ≥3 lines for the workflow grep; a recent `v1.5x.x` tag name.

## End state

After all 3 phases:

- `package.json` + `package-lock.json` committed at repo root.
- `playwright.config.ts` + `tests/js/global-setup.ts` + `tests/js/global-teardown.ts` + `tests/js/smoke.spec.ts` committed.
- `.gitignore` excludes `node_modules/`, `playwright-report/`, `test-results/`, `tests/js/.cache/`.
- `.github/workflows/ci.yml` `lint-misc` job includes a Node-22 setup, Playwright-browser cache, and a `JS smoke tests` step.
- 7 smoke tests in `tests/js/smoke.spec.ts`, all green.
- CI total runtime delta ≤ 30 s amortized after first-run cache primes.
- Manual operator runbook addition (if any) tracked separately as a `docs/operations.md` line; the plan itself doesn't touch operator-facing docs.

After Phase 3 (and any future expansions): the JS regression gate is structurally equivalent to the Python pytest and PHP PHPUnit gates — a single CI green/red signal that fails loud on `pageerror`, console errors, or non-200 responses.
