# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Removed
- Legacy MySQL sync pipeline: `scripts/{import_from_dump,sync_legacy_observations,load_observations_sqlite,link_sources,dump_and_import}.*`
  and `systemd/kayak-sync.{service,timer,sh}`. The Python fetch pipeline now
  replaces all data previously imported from the legacy MySQL databases.
- `[mysql]` optional dependency (PyMySQL) from `pyproject.toml`.
- MySQL `DATABASE_URL` example from `.env.example`.
- Stale "(replaces X.C)" docstring tags across `src/kayak/` modules and related
  `Mirrors DataDB::*` references. The C++ source they pointed at is no longer
  in the tree.
- "Legacy C++ Code" section from `CLAUDE.md`.

### Added
- GitHub Actions CI workflow (lint, typecheck, test on Python 3.13)
- Makefile for common development commands
- CONTRIBUTING.md with development workflow guide
- Pre-commit hooks configuration (ruff + mypy)
- CHANGELOG.md version tracking
- Comprehensive docstrings on all ORM model classes and parser classes
- Health check script for operational monitoring
- Systemd failure notification support

### Changed
- Refactored `build.py`: extracted constants, split `_build_html_table` into
  focused sub-functions (`_filter_visible_rows`, `_compute_gauge_groups`,
  `_format_cell_value`)
- Expanded README.md with architecture diagram, CLI reference, API endpoints,
  and documentation links

### Fixed
- Unused `timedelta` import in NWPS parser
- Type errors in `fetch_usgs_ogc.py` gauge-pair set construction
- Removed incorrect Flask references from `.env.example`

### Improved
- Test coverage: 77% to 79% (build.py: 42% to 90%, new decimate tests)
- Total test count: 304 to 529

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
