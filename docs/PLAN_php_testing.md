# Plan — fuller PHP unit + integration tests

**Status:** In progress (branch `php-test-coverage`, started 2026-05-25), on top of
the level-9 + strict-rules work merged in #29.

## Context / starting state

- 65 source files under `php/`, 24 test files / 172 tests, **~9.5% measured line
  coverage** (pcov), CI floor 5% (`scripts/check-php-coverage.sh`,
  `FLOOR_PERCENT=5` in `ci.yml`).
- **Architecture in our favour:** page files (`description.php`, `reach.php`, …)
  are thin shims that validate input and call a handler —
  `handle_description_detail(get_db(), $id, …)`. The logic lives in ~29
  `PDO`-taking functions (`handle_*`, `_render_*`, `_load_*`, `_compute_*`,
  `_derive_*`, `_aggregate_*`) plus pure helpers, all now carrying precise row
  shapes from #29.
- **The measurement blocker:** the `*IntegrationTest` files spawn a `php -S`
  subprocess via `IntegrationTestCase`; pcov runs in the test-runner process and
  cannot see across the boundary, so that broad behavioural coverage counts as
  ~0%. `exit()`/`die()` appears in 31 files (the handlers' 404/400/403 paths) —
  the only real in-process blocker.

## Goals / non-goals

- **Goals:** coverage that reflects reality; fast deterministic unit tests for the
  math/helpers; in-process functional tests that exercise *and measure* the
  handler bulk incl. edge branches; integration tests focused on what only E2E
  catches; a ratcheting floor.
- **Non-goals:** 100% coverage; rewriting the app; testing trivial static pages
  (`about`, `privacy`, `disclaimer`, `logout`) beyond a smoke assertion.

## Reproduce / verify

```bash
# local coverage needs a driver (CI uses pcov):
pecl install pcov                      # one-time; ini: pcov.enabled=1, pcov.directory=.
vendor/bin/phpunit --coverage-text     # or --coverage-html=build/cov
composer test                          # plain run, no coverage
FLOOR_PERCENT=5 scripts/check-php-coverage.sh coverage.xml   # the CI gate
composer analyse                       # new test shapes must stay level-9 clean
```

## Phases

### Phase 0 — make coverage honest (foundational; do first)
Everything else is guesswork until the number reflects the handlers.
- **0a. In-process functional harness.** A `FunctionalTestCase` that seeds a tmp
  SQLite (same `levels init-db` schema the integration harness uses), sets
  `$_GET`/`$_POST`/`$_SERVER`/`$_COOKIE`, `ob_start()`s, `require`s the handler,
  asserts on captured HTML/JSON. Runs **in the runner process → pcov counts it**.
  `header()` is harmless while output stays buffered (`headers_sent()` false).
- **0b. An `exit()` seam.** Route HTTP termination through one testable chokepoint
  — `includes/error.php::render_error_page()` and the `*_or_404` loaders throw an
  `HttpExitException(code, message)` that the front-controller shims catch and
  convert to `http_response_code()+exit`; tests assert on the thrown code. Small,
  mechanical, arguably-better-design refactor. Happy-path functional tests need it
  *not at all*, so Phase 3 can start before this lands.
- **Decision:** in-process functional tests are the coverage engine; do **not**
  build subprocess-coverage merge (pcov's `\pcov\collect()` + `php-code-coverage`
  merge is possible but low marginal value once functional tests cover the same
  handlers). Integration tests stay behavioural E2E, uncounted.
- **Exit:** a sample handler shows real line-% in clover; `FunctionalTestCase` +
  `HttpExitException` committed; `composer test` green.

### Phase 1 — test-data factory layer
Per-class `seedDatabase()` is verbose. Build factories (`makeReach`, `makeGauge`,
`linkGaugeSource`, `makeObservation`, `makeLatestGaugeObservation`,
`makeReachClass`, `makeEditor`, `makeChangeRequest`) returning ids, defaults +
overrides, writing through the real schema. Shared by functional + integration.

### Phase 2 — unit tests for pure helpers (fast, high coverage-credit)
Each gets happy + edge (empty / null / boundary / malformed):
`lttb`, `svg_plot` (`nice_axis`, bands, gradient + **elevation** branch),
`svg_plot_rating` (`derive_rating_lookup`, `rate_*` interp + clamp + null),
`validate` (`validate_date`, `date_ts`, coords), `sanity`, `class_tiers`,
`source_url`, `html`, `gauge_map` (geom parse), `gauge_plots_filter`
(multi-source mean-of-means), `mail` (CR/LF header-injection strip), `turnstile`.

### Phase 3 — in-process functional tests for the ~29 handlers (the bulk)
Per handler: happy path + branches — empty result sets, missing optional fields,
no-gauge reach, `hidden=1`, date-range vs default window, status classification
(low/okay/high/unknown), multi-source aggregation, gradient/elevation render.
Error paths (404/400/403) via the Phase-0b seam. This is where measured % jumps.

### Phase 4 — fuller integration (E2E) tests — keep for what only E2E catches
Build on `IntegrationTestCase` (`request`, `seedEditorSession`,
`assertNoBareInlineScript`):
- HTTP/headers: status, `Content-Type`, `Cache-Control`, CSP.
- **Auth/security (highest value):** magic-link consume/expire/single-use replay;
  session create/revoke; CSRF double-submit accept + reject; editor-vs-maintainer
  authz on every gated endpoint; open-redirect/`next` safety; approve-race (exists).
- Per-endpoint edge matrix: 400/404/403, validation failures, banned/rate-limited.

### Phase 5 — ratchet the gate
Step `FLOOR_PERCENT` (5 → 25 → 40 → 60) as phases land; add "coverage must not
decrease" + a target for new/changed lines.

## Risk-based sequencing
1. **Tier 1 (security + data-mutation):** `auth`/`auth_magic_link`, `review_logic`
   (approve **mutates** the DB), `propose_handler` (validation), CSRF/session.
2. **Tier 2 (core read paths):** description/reach/gauge detail, custom/picker.
3. **Tier 3 (math/helpers):** lttb, svg, classifiers.
4. **Tier 4:** static pages — smoke only.

## Convergence ("iterate until no new findings")
After each phase: `composer test` green, `composer analyse` 0, coverage % up,
no new uncovered branches in the phase's scope. Re-review the diff (do the new
tests actually exercise the branches they claim? any seam that changed behaviour?)
and fix until a pass yields no fresh gaps. Ratchet the floor, commit per phase.
