# Changelog

All notable changes to this project will be documented in this file.

> Curated and thematic — see `git log` for the exhaustive commit history.
> The `[Unreleased]` section captures the gist of work since 2026-04-22
> grouped by theme rather than line-by-line.

## [Unreleased]

### Added
- **Cert-expiry monitor** (P0.2 of `docs/PLAN_pre_release_followup.md`):
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
- **DNS cutover fast-path plan** (`DNS.CHANGEOVER-fastpath.md`): uses
  the DreamHost-issued LE cert at `/etc/nginx/certs/levels.wkcc.org.*`
  as a bridge until DNS propagates, then `certbot --nginx --expand` to
  add `levels.wkcc.org` as a third SAN — bypasses the DNS-01 +
  ClubExpress-CNAME-delegation work of the original `DNS.CHANGEOVER.md`.
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
  `DNS.CHANGEOVER-fastpath.md`).

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
