# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Kayak aggregates real-time river level, flow, gage, and temperature data from government agencies (USGS, NOAA, USACE, USBR, IDWR, etc.) for the Willamette Kayak and Canoe Club (levels.wkcc.org). The project is a Python package (`kayak`) with a PHP web layer.

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

`init-db` seeds states, sources, and fetch URLs from `data/sources.yaml`.

## Build and Development Commands

```bash
pip install -e ".[dev]"              # Install in editable mode with dev deps (pytest, ruff, mypy)
# or: uv sync --locked --all-extras (matches CI)

levels --help                        # CLI entry point (registered in pyproject.toml)
levels init-db                       # Create tables, seed states/sources, stamp migrations
levels migrate                       # Apply any pending data/db/migrations/*.sql files
levels pipeline                      # fetch → fetch-usgs-ogc → calc-rating → update-gauge-cache → calculator → build
levels build                         # Generate static HTML/CSV/text to public_html/

# Less-common subcommands (see `levels <cmd> --help` for details)
levels fetch                         # One shot of the pipeline's first stage
levels fetch-usgs-ogc                # Fetch USGS OGC continuous data for gauges with usgs_id
levels merge                         # Median-fuse observations across a gauge's sources (manual-only)
levels calc-rating                   # Interpolate via rating tables (dormant until rating_data loaded)
levels calculator                    # Evaluate synthetic gauge expressions
levels decimate                      # Thin old observations (daily via kayak-decimate timer)
levels seed-maintainer --email …     # Create/promote a maintainer editor row
levels trace --putin … --takeout …   # Trace a reach along NHD HR flowlines
levels assign-huc                    # Assign HUC12 codes to reaches (requires [geo] extra)
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

Ruff config: Python 3.13 target, 100-char line length, rules `E W F I UP B SIM RUF`. Configured in `pyproject.toml`.

### Running the PHP Web Layer

```bash
php -S localhost:8000 -t public_html  # Serve PHP pages + static build output
```

### Stream Tracing

```bash
bash scripts/extract_trace_data.sh       # One-time: pre-extract HUC4 GDBs → fast GPKGs (~20 min)
python3 scripts/trace_reach.py \         # Trace a reach between put-in and take-out
    --putin LAT,LON --takeout LAT,LON --name "River Name"
```

Traces stream paths using NHDPlus HR HydroSeq network data. Requires `Trace-cache/` with raw NHD HR GDB ZIPs and/or pre-extracted GPKGs (gitignored, ~5 GB). See `docs/tracing.md` for full documentation.

## Architecture

### Data Pipeline (`levels pipeline`)

Runs these steps in order:

1. **fetch** — reads `data/sources.yaml`, fetches URLs, dispatches to registered parsers, stores `Observation` rows
2. **fetch-usgs-ogc** — fetches USGS data via the OGC API for gauges with `usgs_id`
3. **calc-rating** — interpolates missing flow from gage height (or vice versa) using `Rating`/`RatingData` tables
4. **update-gauge-cache** — recomputes gauge-level latest observation values
5. **calculator** — evaluates `CalcExpression` formulas referencing `LatestObservation` values
6. **build** — generates per-state HTML pages, CSV, and text files to `public_html/`; inlines CSS and SVG sparklines

**Note:** `merge` (for gauges with multiple sources) is not part of the pipeline — run `levels merge` manually when needed.

### Two-Layer Web Architecture

**Python (static generation):** `levels build` writes self-contained HTML pages to `public_html/` with inlined CSS (from `src/kayak/web/static/style.css`) and SVG sparklines. These are the main river levels tables.

**PHP (dynamic pages):** PHP files in `php/` handle interactive features — description pages with plots, data APIs, editing, the reach picker, and source/gauge browsers. Both layers share the same database (`SQLITE_PATH` env var for PHP, `DATABASE_URL` for Python).

### Database

Single normalized SQLite database (`kayak.db`). Schema defined in `src/kayak/db/models.py` (SQLAlchemy 2.x ORM, 28 tables). Key tables:

- `source` / `gauge` / `gauge_source` — data sources and physical gauge stations
- `observation` — time-series data (source_id, observed_at, data_type, value)
- `latest_observation` / `latest_gauge_observation` — cached most-recent reading with delta_per_hour
- `reach` / `reach_state` / `reach_class` / `reach_guidebook` — paddleable runs with state, class, and guidebook relationships
- `fetch_url` / `calc_expression` — how to obtain data (fetch vs. calculate)
- `rating` / `rating_data` — gage height ↔ flow conversion tables (dormant — reserved for per-gauge rating curves)
- `editor` / `editor_session` / `editor_magic_link` / `maintainer_credential` — Phase 1 editor accounts + session cookies
- `change_request` / `change_request_attachment` / `edit_history` — proposal queue + audit trail
- `huc_name` — WBD HUC2/4/6/8/10/12 name lookup populated by `levels assign-huc`
- `schema_migrations` — tracks applied `data/db/migrations/*.sql` versions

Schema evolution:
1. Add or change the model in `models.py`.
2. For new fresh-DB shape only, `levels init-db` (re)creates tables via `Base.metadata.create_all()` and stamps every discovered migration file as applied.
3. For changes that need to land on an existing DB (ALTER / DROP / rename / CHECK), add a new `data/db/migrations/NNNN_description.sql` and run `levels migrate` — SQL runs in file-order inside a transaction; the row in `schema_migrations` records completion.

### Parser System

Parsers inherit from `BaseParser` (in `src/kayak/parsers/base.py`) and register via `@register("name")` decorator. The `parse(text)` method feeds lines to `parse_line()` (abstract). `ensure_all_loaded()` imports all parser modules to trigger registration. Parser names match entries in `data/sources.yaml`.

### CLI Pattern

Each subcommand module in `src/kayak/cli/` exposes `addArgs(subparsers)` and sets `args.func` as the handler. Global logging flags (`--debug`, `--verbose`, `--logfile`) added via `kayak.cli.logger.addArgs`.

## Key Conventions

- **Source layout:** Python package lives under `src/kayak/`; pytest config sets `pythonpath = ["src"]`
- **Configuration:** All settings via env vars or `.env` file; `kayak.config` checks `~/.config/kayak/.env` first, then falls back to default `load_dotenv()` search; `kayak.config_data` uses `@lru_cache` for YAML files in `data/`
- **Database access:** `kayak.db.engine.get_session(url)` provides sessions; CLI commands manage session lifecycle
- **Upsert pattern:** `store_observation()` uses SQLite `ON CONFLICT DO UPDATE`
- **Test isolation:** Every test gets a fresh in-memory SQLite engine and a transactional session that rolls back
- **Test fixtures:** `tests/conftest.py` provides `engine`, `session`, `sample_source`, `sample_gauge`, `sample_reach`, `linked_source_gauge`
- **PHP DB connection:** `php/includes/db.php` reads `SQLITE_PATH` env var; SQLite PDO only

