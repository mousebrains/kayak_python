# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Kayak aggregates real-time river level, flow, gage, and temperature data from government agencies (USGS, NOAA, USACE, USBR, IDWR, etc.) for the Willamette Kayak and Canoe Club (levels.wkcc.org). The project has been rewritten from C++ CGI into a Python package (`kayak`) with a PHP web layer. The legacy C++ code remains in `src/*.C`/`src/*.H` but is no longer built.

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

**PHP (dynamic pages):** PHP files in `php/` handle interactive features — description pages with plots, data APIs, editing, the section picker. Both layers share the same database via `DATABASE_URL` environment variable.

### Database

Single normalized SQLite database (`kayak.db`) for development, MySQL for production. Schema defined in `src/kayak/db/models.py` (SQLAlchemy 2.x ORM, 18 tables). Key tables:

- `source` / `gauge` / `gauge_source` — data sources and physical gauge stations
- `observation` — time-series data (source_id, observed_at, data_type, value)
- `latest_observation` — cached most-recent reading with delta_per_hour
- `section` — river runs with metadata, coordinates, levels
- `fetch_url` / `calc_expression` — how to obtain data (fetch vs. calculate)
- `rating` / `rating_data` — gage height ↔ flow conversion tables

Alembic manages migrations (`alembic upgrade head`, `alembic revision --autogenerate -m "..."`).

### Parser System

Parsers inherit from `BaseParser` (in `src/kayak/parsers/base.py`) and register via `@register("name")` decorator. The `parse(text)` method feeds lines to `parse_line()` (abstract). `ensure_all_loaded()` imports all parser modules to trigger registration. Parser names match entries in `data/sources.yaml`.

### CLI Pattern

Each subcommand module in `src/kayak/cli/` exposes `addArgs(subparsers)` and sets `args.func` as the handler. Global logging flags (`--debug`, `--verbose`, `--logfile`) added via `kayak.cli.logger.addArgs`.

## Key Conventions

- **Source layout:** Python package lives under `src/kayak/`; pytest config sets `pythonpath = ["src"]`
- **Configuration:** All settings via env vars or `.env` file; `kayak.config` uses `python-dotenv`; `kayak.config_data` uses `@lru_cache` for YAML files in `data/`
- **Database access:** `kayak.db.engine.get_session(url)` provides sessions; CLI commands manage session lifecycle
- **Upsert pattern:** `store_observation()` uses SQLite `ON CONFLICT DO UPDATE` / MySQL `ON DUPLICATE KEY UPDATE`
- **Test isolation:** Every test gets a fresh in-memory SQLite engine and a transactional session that rolls back
- **Test fixtures:** `tests/conftest.py` provides `engine`, `session`, `sample_source`, `sample_gauge`, `sample_section`, `linked_source_gauge`
- **PHP DB connection:** `php/includes/db.php` reads `DATABASE_URL` env var; supports both MySQL PDO and SQLite PDO

## Legacy C++ Code

The original C++ codebase remains in `src/*.C`/`src/*.H` with `src/Makefile`. It used `.C`/`.H` extensions, mysql++ library, three separate MySQL databases, and CGI binaries. This code is no longer built but is retained for reference. The legacy per-station table schema (e.g., `flow_14306500`) can be synced to the new normalized schema via `scripts/sync_legacy_observations.py`.
