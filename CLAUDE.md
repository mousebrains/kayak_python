# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Kayak aggregates real-time river level, flow, gage, and temperature data from government agencies (USGS, NOAA, USACE, USBR, IDWR, etc.) for the Willamette Kayak and Canoe Club (levels.wkcc.org). The project has been rewritten from C++ CGI into a Python package (`kayak`) with a PHP web layer. The legacy C++ code remains in `src/*.C`/`src/*.H` but is no longer built.

## Local Development Setup

The development environment uses these paths (configured in `~/.config/kayak/.env`):

| Component | Path |
|---|---|
| Virtual environment | `/home/pat/.venv` |
| Configuration | `~/.config/kayak/.env` |
| SQLite database | `/home/pat/DB/kayak.db` |
| Document root | `/home/pat/public_html` → symlink to `kayak/public_html` |

`config.py` checks `~/.config/kayak/.env` before falling back to the default `load_dotenv()` search. PHP gets `SQLITE_PATH` from nginx `fastcgi_param`.

POSIX ACLs grant `www-data` access: execute-only on `/home/pat` and `/home/pat/kayak` (traverse), read on `public_html` and `php/` (with default ACLs for new files), read-write on `/home/pat/DB`.

### Quick start

```bash
python3 -m venv /home/pat/.venv
/home/pat/.venv/bin/pip install -e ".[dev]"
/home/pat/.venv/bin/levels init-db       # Creates schema, seeds states/sources/fetch_urls
/home/pat/.venv/bin/levels pipeline      # Fetch live data and generate HTML
```

`init-db` seeds states, sources, and fetch URLs from `data/sources.yaml`. Gauges and reaches must be imported separately from the legacy MySQL dump (see Migration & Sync Scripts below).

## Build and Development Commands

```bash
pip install -e ".[dev]"              # Install in editable mode with dev deps (pytest, ruff, mypy)
pip install -e ".[dev,mysql]"        # Also include PyMySQL for MySQL support

levels --help                        # CLI entry point (registered in pyproject.toml)
levels init-db                       # Create tables, seed states/sources from data/*.yaml
levels pipeline                      # Run full pipeline: fetch → calc-rating → merge → calculator → build
levels build                         # Generate static HTML/CSV/text to public_html/
```

### Testing

```bash
pytest                               # Run all tests (uses in-memory SQLite, no disk I/O)
pytest tests/test_models.py          # Run a single test file
pytest -k test_store_observation     # Run a specific test by name
pytest -m "not slow"                 # Skip slow/integration tests
pytest --cov=kayak                   # Run with coverage
```

### Linting

```bash
ruff check src/ tests/               # Lint
ruff check --fix src/ tests/         # Lint with auto-fix
ruff format src/ tests/              # Format
mypy src/                            # Type check
```

Ruff config: Python 3.11 target, 100-char line length, rules `E W F I UP B SIM RUF`. Configured in `pyproject.toml`.

### Running the PHP Web Layer

```bash
php -S localhost:8000 -t public_html  # Serve PHP pages + static build output
```

## Architecture

### Data Pipeline (`levels pipeline`)

Runs these steps in order:

1. **fetch** — reads `data/sources.yaml`, fetches URLs, dispatches to registered parsers, stores `Observation` rows
2. **calc-rating** — interpolates missing flow from gage height (or vice versa) using `Rating`/`RatingData` tables
3. **merge** — for gauges with multiple sources, merges observations using median at each timestamp
4. **calculator** — evaluates `CalcExpression` formulas referencing `LatestObservation` values
5. **build** — generates per-state HTML pages, CSV, and text files to `public_html/`; inlines CSS and SVG sparklines

### Two-Layer Web Architecture

**Python (static generation):** `levels build` writes self-contained HTML pages to `public_html/` with inlined CSS (from `src/kayak/web/static/style.css`) and SVG sparklines. These are the main river levels tables.

**PHP (dynamic pages):** PHP files in `php/` handle interactive features — description pages with plots, data APIs, editing, the reach picker, and source/gauge browsers. Both layers share the same database (`SQLITE_PATH` env var for PHP, `DATABASE_URL` for Python).

### Database

Single normalized SQLite database (`kayak.db`) for development, MySQL for production. Schema defined in `src/kayak/db/models.py` (SQLAlchemy 2.x ORM, 18 tables). Key tables:

- `source` / `gauge` / `gauge_source` — data sources and physical gauge stations
- `observation` — time-series data (source_id, observed_at, data_type, value)
- `latest_observation` — cached most-recent reading with delta_per_hour
- `reach` — river runs with metadata, coordinates, levels
- `fetch_url` / `calc_expression` — how to obtain data (fetch vs. calculate)
- `rating` / `rating_data` — gage height ↔ flow conversion tables

Alembic manages migrations (`alembic upgrade head`, `alembic revision --autogenerate -m "..."`).

### Parser System

Parsers inherit from `BaseParser` (in `src/kayak/parsers/base.py`) and register via `@register("name")` decorator. The `parse(text)` method feeds lines to `parse_line()` (abstract). `ensure_all_loaded()` imports all parser modules to trigger registration. Parser names match entries in `data/sources.yaml`.

### CLI Pattern

Each subcommand module in `src/kayak/cli/` exposes `addArgs(subparsers)` and sets `args.func` as the handler. Global logging flags (`--debug`, `--verbose`, `--logfile`) added via `kayak.cli.logger.addArgs`.

## Key Conventions

- **Source layout:** Python package lives under `src/kayak/`; pytest config sets `pythonpath = ["src"]`
- **Configuration:** All settings via env vars or `.env` file; `kayak.config` checks `~/.config/kayak/.env` first, then falls back to default `load_dotenv()` search; `kayak.config_data` uses `@lru_cache` for YAML files in `data/`
- **Database access:** `kayak.db.engine.get_session(url)` provides sessions; CLI commands manage session lifecycle
- **Upsert pattern:** `store_observation()` uses SQLite `ON CONFLICT DO UPDATE` / MySQL `ON DUPLICATE KEY UPDATE`
- **Test isolation:** Every test gets a fresh in-memory SQLite engine and a transactional session that rolls back
- **Test fixtures:** `tests/conftest.py` provides `engine`, `session`, `sample_source`, `sample_gauge`, `sample_reach`, `linked_source_gauge`
- **PHP DB connection:** `php/includes/db.php` reads `SQLITE_PATH` env var; SQLite PDO only

## Migration & Sync Scripts

Scripts in `scripts/` handle data migration between the legacy MySQL databases and the new schema:

| Script | Purpose |
|---|---|
| `import_from_dump.py` | Import production MySQL dump (`levels_todo`) into local SQLite. Populates gauges, reaches, ratings, and optionally observations. Required for local dev setup after `init-db`. |
| `migrate_legacy_to_wkcclevels.py` | Full one-time migration from legacy `levels_todo`/`levels_data`/`levels_page` → `wkcclevels` MySQL. Drops and recreates all 18 tables. |
| `sync_observations.py` | Incremental sync from `levels_data` → `wkcclevels`. Uses high-water marks (`MAX(observed_at)` per source/data_type) to fetch only new rows. Runs on cron (twice daily at 6:00/18:00). |
| `sync_legacy_observations.py` | Sync legacy observations into local SQLite or MySQL. Supports `--days N` window. Used for dev setup. |
| `load_observations_sqlite.py` | Load `observation.csv` and `latest_observation.csv` dumps into a SQLite database. Self-contained (stdlib only, no dependencies). |

```bash
# Full migration (destructive — drops target tables first)
python3 scripts/migrate_legacy_to_wkcclevels.py

# Incremental sync (production cron job)
python3 scripts/sync_observations.py              # high-water mark (default)
python3 scripts/sync_observations.py --days 7     # force last 7 days
python3 scripts/sync_observations.py --dry-run    # show counts only

# Dump observations from MySQL for SQLite import
python3 scripts/load_observations_sqlite.py --db kayak.db

# Via SSH tunnel (all MySQL scripts)
ssh -L 3307:mysql.wkcc.dreamhosters.com:3306 tpw@levels.wkcc.org -N &
python3 scripts/sync_observations.py --legacy-host 127.0.0.1 --legacy-port 3307
```

## Legacy C++ Code

The original C++ codebase remains in `src/*.C`/`src/*.H` with `src/Makefile`. It used `.C`/`.H` extensions, mysql++ library, three separate MySQL databases, and CGI binaries. This code is no longer built but is retained for reference.

## Python Version Compatibility

The codebase targets Python 3.11+ (`datetime.UTC`, `enum.StrEnum`). The production server runs Python 3.13.
