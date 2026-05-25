# Changelog

All notable changes to this project will be documented in this file.

> Curated and thematic — see `git log` for the exhaustive commit history.
> The `[Unreleased]` section captures the gist of work since 2026-04-22
> grouped by theme rather than line-by-line.

## [Unreleased]

### Added
- **Gradient profile**: per-reach `max_gradient` plus a statistically-binned
  gradient chart on reach-detail pages (DEM-sampled; canyon-trace artifacts
  flagged via `reach.gradient_unreliable`).
- **Reach-detail elevation profile**: the gradient chart overlays an elevation
  line (right axis) with an elevation-first cursor readout, and the per-reach
  details collapse to four shared lines (Watershed / Length / Elevation / Flow)
  on both the description and reach pages.
- **Geometry snapshot + recovery path**: `reach.geom` is snapshotted to
  `data/db/reaches.json` (~5 m Douglas–Peucker simplified, excluded from
  `reach.csv`) and applied with `scripts/import_metadata.py --geom-only` — the
  supported way a dev re-trace's geometry reaches prod.

### Fixed
- **CSP dashboard accuracy**: `_internal/`'s "Recent CSP violations" table no
  longer mislabels proxy/extension-injected inline scripts as "Same-origin
  (our code)". Inline/eval/wasm-eval violations carry the *document* URL as
  their source-file, so a same-origin value there is an injection (e.g. Google
  Web Light's `google-proxy-*` fleet), not authored code — now bucketed
  "Injected (proxy/extension)". Also fixes the `violated`/`blocked` columns
  rendering `—` (they read pre-normalization log keys). `csp_classify()`
  extracted to `php/includes/csp_classify.php` with unit tests.
- **rDNS resolver bounded** so `kayak-status.service` no longer times out on
  slow reverse lookups (wall-clock budget + negative-cache backoff).
- **Deploy path**: the documented quick-start now loads the metadata snapshot
  so a fresh DB renders data; `systemd/install.service.sh` installs all 15
  timers; `deploy/SETUP.md` deploys the live `conf/sites/` split instead of the
  retired `deploy/levels`; migrations 0052/0054/0055/0056 no longer carry stray
  `BEGIN/COMMIT` that broke the runner's transaction wrapper.
- **Docs drift**: schema-doc table count corrected to 24/25 and the dropped
  `maintainer_credential` table removed; hardware specs corrected to the
  Hetzner CPX11 (2 vCPU / 2 GB / 40 GB).
- **HUC4 detection** resolves by nearest flowline with put-in/take-out
  agreement, fixing 88/407 reaches that mis-detected near basin divides (was:
  the first GPKG whose flowline extent contained the put-in).
- **Metadata recovery hardened**: `scripts/import_metadata.py` upserts on the
  primary key — preserving `reach.geom` / `fetch_url.last_fetched_at` instead of
  nulling them on a full import — and reports the rows actually applied;
  `docs/migrations.md` now documents the real from-scratch rebuild runbook (the
  prior text wrongly declared rebuild impossible). Covered by a new export→import
  round-trip test.
- **`deploy.sh` applies committed geometry**: a changed `data/db/reaches.json`
  now triggers `import_metadata.py --geom-only`, so a dev re-trace's geometry
  reaches prod instead of silently going stale.
- **Gradient elevation line themed** via `.gp-elev` CSS (legible in dark mode)
  rather than a hardcoded inline color.
- **`docs/security/` audit anchors repointed** to the post-2026-05-14-split
  files (`auth_magic_link` / `propose_handler` / `review_handler`), keyed on
  function names so they survive future line drift.

### Changed
- Pinned `ruff` in pre-commit/CI to match `uv.lock` and stop formatter drift.
- Applied biome `--write` cleanups to the static JS (optional chaining, unused
  catch bindings).
- Dependabot dependency bumps: the composer dev group and the GitHub Actions
  group.
- **`scripts/` gated in CI**: ruff over all of `scripts/`; mypy over the core
  metadata scripts (`import_metadata` / `export_metadata`), with the package
  marked typed (`py.typed`).
- **Internal dedup**: `_localize` hoisted onto `BaseParser`; `check-reaches`
  returns an exit code (mapped in `main.py`, which also now surfaces the codes
  other handlers like `analyze-logs` returned but previously had discarded)
  instead of calling `sys.exit`; `M_TO_FT` given a canonical home;
  `.gitattributes` collapses the opaque `reaches.json` / `huc_name.csv`
  snapshot diffs (reach.csv stays a readable text diff).
- **PHP type-safety hardened**: `php/` now runs PHPStan at **level 9** with the
  **full** `phpstan/phpstan-strict-rules` (no toggles). The old level-8
  grandfather baseline was cleared by fixing every find at the source; all
  strict-rules finds — including the `booleansInConditions` and short-ternary
  families (boolean conditions made explicit, `$a ?: $b` removed) — fixed to
  zero with hundreds of behaviour-preserving edits; the residual level-9
  `mixed`-typing finds are captured in a fresh *shrinking* `phpstan-baseline.neon`
  (634 entries) so new code is held to level 9. 172 phpunit tests stay green.
  See `docs/PLAN_phpstan_level9_strict.md`.

## [1.1.1] - 2026-05-21

### Added
- **Operator status page at `/_internal/status`**: nightly-regenerated HTML
  with six collapsible sections — 4 h human/bot buckets, hits by country
  (full English names + ISO codes, geoIP via DB-IP City Lite), US states &
  Canadian provinces (subdivisions from the same mmdb), hits by URL (query
  strings stripped, assets filtered, log glob narrowed to `levels-*` so
  the default-vhost `blocked-access.log` port-scanner noise stays out),
  per-IP detail with rDNS + country + click-to-sort columns, and systemd
  job status that auto-opens with a red `N failed` badge when any
  `kayak-*.service` has a non-zero exit. Plus disk + memory (df +
  /proc/meminfo with WARN/FAIL flags) and backups + TLS-cert expiry.
  Served behind the existing `require_maintainer()` via a PHP wrapper
  that `readfile()`s `/home/pat/kayak/var/status.html`. Rendered by
  `kayak-status.timer` at 03:30 daily.
- **Bot classifier — `_is_root_hammer`**: `paths == {"/"}` AND
  `hits >= 3` → `root-only`. Real browsers always pull `/style.css` +
  `/static/*.js` + sparklines on first visit (and emit conditional
  GETs on revisits), so IPs hitting only `/` many times are bot-shaped.
  Catches ~5 000 hits / ~1 800 IPs per day that previously inflated
  the human count (data-center IPv6 ranges and bare-Chrome scanners).
- **Better Stack monitor classification**: new
  `kayak.analytics.monitors` consumes the published IP list at
  `uptime.betterstack.com/ips-by-cluster.json` (weekly disk cache,
  fail-open on fetch error). Probes from those 34 IPs now land in a
  `monitor` bucket rather than mixing with attack scanners.
- **rDNS + geoIP persistent caches**: `var/rdns_cache.json` and
  `var/geoip/lookup_cache.json` hold `[name, last_seen]` tuples with
  a 60-day TTL, so subsequent renders only touch the network / mmdb
  for newly-seen IPs.
- **Legacy `/cgi/*` redirects**: `/cgi/picker/`, `/cgi/png?…`,
  `/cgi/makePage?…` (from the old C++ site) now 301 to `/` with the
  legacy query string stripped, mirroring the existing pre-2026
  `/?P=`/`/?D=`/`/?f=` redirect block in
  `conf/snippets/levels-common.conf`. Real users hitting stale
  bookmarks / RSS links land on the homepage instead of a 404.
- **Low-disk + swap warnings** in `scripts/health-check.sh`: disk
  WARN ≥70 % / FAIL ≥85 %; swap WARN if used ≥10 % AND
  MemAvailable <400 MB (conjunction prevents false alarms on idle
  hosts that briefly touched swap). Trips the existing hourly
  `OnFailure=kayak-notify-failure@%n.service` cascade. Required
  relaxing `ProcSubset=pid` → `ProcSubset=all` on
  `kayak-healthcheck.service` so the script can read `/proc/meminfo`.

### Fixed
- **rDNS no longer blocks the main thread**: `rdns()` previously fell
  through to a synchronous `socket.gethostbyaddr` for any IP that
  didn't make it into the `warm_rdns` cache — ~10 s of blocking per
  unwarmed IP turned a first-time render with thousands of unresolved
  addresses into an hours-long stall. Now strictly cache-only;
  uncached IPs render as `-` and are retried on the next run.
- **PHP-FPM `open_basedir`** in `deploy/kayak-fpm-pool.conf` extended
  with `/home/pat/kayak/var` so the status-page wrapper can
  `readfile()` the cached HTML. Without it the wrapper failed
  `is_readable()` silently and rendered the "not yet generated"
  fallback even when the file was on disk and ACL-readable.

## [1.1.0] - 2026-05-21

### Added
- **Montana coverage** (#10): USGS gauges for the Montana basins, plus a
  per-state gauges page reachable via `/gauges/<state>.html` so the
  state filter on the reaches index has a sibling for gauge-first
  browsing.
- **MT AW reach importer** (`df9c99c`): one-off seed of the 16
  curated American Whitewater reaches that anchor the Montana rollout.
- **Reach location in page headings** (#11): `description.php` and
  `reach.php` now append the river segment + nearest town to the H1
  heading, so a bookmarked reach is identifiable at a glance.

### Changed
- **Multi-state reaches appear under every linked state** (`2a6a7a8`):
  `reach_state.data_state` is emitted as a CSV list so the client-side
  filter renders the reach under each state, not just the first.
- **Filter bar stays collapsed on hash-filter arrival** (`40c49c0`):
  arriving via `#filter=…` no longer auto-opens the filter drawer — the
  intent of the fragment is to apply, not to invite further editing.

### Fixed
- **`analyze-logs chunked`/`humans` no longer hangs on slow rDNS**
  (`6405da5`): `socket.gethostbyaddr` is a blocking C call that ignores
  `socket.setdefaulttimeout`. Pre-resolve the IP set in parallel under
  a 10 s wall-clock budget; misses fall back to `""`.
- **CSP-report filter drops modern Chrome/Edge extension noise**
  (`ee82bed`): newer browsers truncate the `source-file` value to the
  bare scheme (`chrome-extension`) rather than the full
  `chrome-extension://<id>/…`. Accept either form so extension-only
  reports stop polluting `csp.log`.
- **Safari/iOS `/apple-touch-icon*.png` probes** (`232778b`): the six
  root-path variants WebKit probes for Home Screen + share previews now
  alias to the existing `/static/icon-180.png` (1,113 hits / 270 unique
  iOS+macOS clients over the post-cutover 48 h were 404ing).

### CI / maintenance
- **pip-audit allowlist for PYSEC-2026-89** (#12): markdown 3.10.2
  false positive — the affected code path isn't exercised by our usage.

## [1.0.0] - 2026-05-19

### Added
- **Cert-expiry monitor** (P0.2 of `docs/done/PLAN_pre_release_followup.md`):
  daily live TLS probe via `kayak-cert-expiry.timer` (User=pat, 3-attempt
  probe per host, union-coverage SAN check); weekly `certbot renew
  --dry-run` via `kayak-cert-renewal-test.timer` (User=root). Both
  heartbeat to healthchecks.io and fire `OnFailure=kayak-notify-failure`.
- **Hourly database backup** (T1.1): `kayak-backup-hourly.{sh,service,timer}`
  with `PRAGMA wal_checkpoint(TRUNCATE)` + `sqlite3 .backup`. Keeps the
  newest 24; drops RPO from 7 days to 1 hour.
- **Config drift detection** (T1.2): `scripts/check-config-drift.sh` +
  `kayak-config-drift.{service,timer}` (User=root). Weekly diff of repo
  `conf/`/`deploy/`/`systemd/` against installed `/etc/` paths; alerts
  on any drift via OnFailure.
- **Operator runbook** at `docs/operations.md` (T1.4) covering health
  endpoints, backup + restore from the new hourly window, partial
  `@no_transaction` migration recovery, pipeline failure triage,
  config-drift triage.
- **DNS cutover plan** (`DNS.CHANGEOVER.md`): uses the DreamHost-issued
  LE cert at `/etc/nginx/certs/levels.wkcc.org.*` as a bridge until DNS
  propagates, then `certbot --nginx --expand` to add `levels.wkcc.org`
  as a third SAN. Single ClubExpress ticket; no DNS-01 dance. (The
  earlier DNS-01-with-CNAME-delegation draft was dropped 2026-05-15
  once the bridge cert path was validated; the prior contents survive
  in git history.)
- **PHP integration tests dump mail to `/tmp`** instead of calling real
  `mail()` (which prod's msmtp dutifully delivered until catching on).
- **Nightly metadata-table snapshot** to `data/db/*.csv` via
  `kayak-metadata-snapshot.timer` — commits cleanly-changing metadata
  to git so the in-repo CSV mirror stays current.
- **`roave/security-advisories`** in `composer.json` require-dev (QW.9):
  install-time gate against known-CVE'd PHP packages.
- **`composer audit` + `npm audit --audit-level=high` in CI** (QW.10).

### Changed
- **nginx vhosts split three ways** (commit `b20f618` + follow-ups):
  the single `conf/levels.nginx` is retired; `conf/sites/levels-
  mousebrains-com`, `conf/sites/levels-test-wkcc-org`, and `conf/sites/
  levels-wkcc-org` each own one hostname; the shared block is in
  `conf/snippets/levels-common.conf`. Per-host access/error logs
  (`/var/log/nginx/levels-*.{access,error}.log`); legacy
  `kayak-{access,error}.log` retired in favor of fail2ban globbing.
- **fail2ban jails** (`jail.local` + `jail.d/kayak-*.conf`) now read
  `/var/log/nginx/levels-*.{access,error}.log` globs — a fourth vhost
  added later needs no fail2ban edit.
- **PHP-FPM pool tuning** (QW.2 + QW.3): pinned
  `date.timezone = UTC` and `request_terminate_timeout = 30`.
- **HTTP fetch response body capped at 50 MB** (QW.4): hostile or
  runaway feed no longer OOMs the pipeline.
- **Pipeline fail-fast** (QW.5): `fetch` or `fetch-usgs-ogc` failure
  short-circuits downstream transforms + build, surfacing the failure
  loudly instead of publishing stale data.
- **PDO `ATTR_EMULATE_PREPARES = false`** (QW.1): defends against a
  future driver swap.
- **Gmail-equivalent email normalization** (QW.7): strip `+tag` and
  dots in the local part for gmail.com / googlemail.com to close the
  per-email magic-link rate-cap bypass.
- **Frontend a11y fixes** (QW.8a/b/c): HUC8 filter pills dedupe on
  same-code-different-basin; sparkline placeholder spans get
  `aria-hidden="true"`; the per-page Weather nav link adapts to the
  active state instead of hardcoding Oregon.
- **`kayak-pipeline.service` sandbox tightened** (QW.6): `ReadWritePaths`
  narrowed from `/home/pat` to specific subdirs. Build staging moved
  from `<output_dir>.staging` (sibling) to `<output_dir>/.staging`
  (subdir) so the systemd namespace setup doesn't fail when the dir
  is rmtree'd between runs.
- **Weekly DB backup unit renamed** to `kayak-backup-weekly.*` to
  disambiguate from the new hourly. Backup filename pattern unchanged
  (`backup-*.db.gz`) so on-disk files remain valid.
- **License stack** (`LICENSE`, `LICENSE-DATA`): layered data + code
  license stack with embedded attribution.

### Removed
- **`mbstring` from CI** (P0.1): prod PHP-FPM lacks it; CI now matches.
  Verified zero `mb_*` references in `src/`, `php/`, `scripts/`,
  `tests/`.
- **Legacy MySQL sync pipeline:**
  `scripts/{import_from_dump,sync_legacy_observations,load_observations_sqlite,link_sources,dump_and_import}.*`
  and `systemd/kayak-sync.{service,timer,sh}`. Python fetch pipeline now
  replaces all data previously imported from the legacy MySQL DBs.
- `[mysql]` optional dependency (PyMySQL) from `pyproject.toml`.
- MySQL `DATABASE_URL` example from `.env.example`.
- "Legacy C++ Code" section from `CLAUDE.md` and "(replaces X.C)"
  docstring tags across `src/kayak/`.

### Operations
- Live host now runs 12 systemd timers (pipeline, healthcheck, hourly +
  weekly backup, decimate, editor-retention, metadata-snapshot, cert-
  expiry, cert-renewal-test, audit-gauges, config-drift, heartbeat).
- DNS cutover to `levels.wkcc.org` planned for mid-May 2026 (see
  `DNS.CHANGEOVER.md`).

## [0.1.0] - 2026-03-01

Initial Python rewrite of the C++ CGI kayak levels system.

### Added
- Python package (`kayak`) with CLI entry point (`levels`)
- Pipeline: fetch, calc-rating, merge, calculator, build
- 9 data source parsers: USGS, NWPS, NWRFC (XML + textplot), USBR, USACE
  (CDA + outflow), WA DOE
- SQLAlchemy 2.x ORM with 18-table normalized schema
- Static HTML generation with inlined CSS and SVG sparklines
- PHP web layer: description pages, plots, reach picker, editor, API
- Observation decimation with LTTB algorithm (hourly + 6-hourly thinning)
- Systemd timers: hourly pipeline, daily decimate, weekly backup
- Database schema documentation with SVG ER diagram
- Production deployment guide (Hetzner + Oracle Cloud)
- Nginx hardening: fail2ban, rate limiting, CSP, bot blocking
- 304 tests with in-memory SQLite fixtures
- Ruff linting + mypy type checking
- Import scripts for legacy MySQL data migration
- Gauge audit script for metadata discovery
- American Whitewater reach integration
- NHD/OSM flowline extraction for river traces
- GeoJSON map generation with Leaflet
