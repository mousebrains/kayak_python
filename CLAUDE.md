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
| Document root | `/home/pat/public_html` (regular directory; populated by `levels build`) |

`config.py` checks `~/.config/kayak/.env` before falling back to the default `load_dotenv()` search. PHP gets `SQLITE_PATH` from nginx `fastcgi_param`.

**`OUTPUT_DIR` convention:** the live host sets `OUTPUT_DIR=/home/pat/public_html` (outside the repo), so `levels build` writes to the nginx docroot and never touches the repo tree. On a separate dev machine, set `OUTPUT_DIR=/home/<user>/public_html_dev` (or similar non-repo path) in `~/.config/kayak/.env` and serve with `php -S localhost:8000 -t "$OUTPUT_DIR"`. The default (unset) writes back into the repo's `public_html/`, which clobbers tracked dev symlinks and drops stray artifacts under `static/`. See `.env.example` for the full rationale.

POSIX ACLs grant `www-data` access: execute-only on `/home/pat` and `/home/pat/kayak` (traverse), read on `public_html` and `php/` (with default ACLs for new files), read-write on `/home/pat/DB`.

### Quick start

```bash
python3 -m venv /home/pat/.venv
/home/pat/.venv/bin/pip install -e ".[dev]"
/home/pat/.venv/bin/levels init-db --no-seed            # Empty schema + stamp migrations
/home/pat/.venv/bin/python scripts/import_metadata.py   # Load gauges/reaches/sources from data/db/*.csv
/home/pat/.venv/bin/levels pipeline                     # Fetch live data and generate HTML
```

`init-db` creates the schema and stamps migrations; `--no-seed` skips the
`sources.yaml` state/source seed so the canonical rows from `data/db/*.csv`
(loaded by `import_metadata.py`) import without duplicate-by-name sources.
Without the metadata load every source is an orphan with no `gauge_source`
link, so `levels pipeline` fails at `orphan-check` and the site renders empty.
A plain `levels init-db` (seeded from `data/sources.yaml`) is enough for a
fetch-only smoke test.

## Build and Development Commands

```bash
pip install -e ".[dev]"              # Install in editable mode with dev deps (pytest, ruff, mypy)
# or: uv sync --locked --all-extras (matches CI)

levels --help                        # CLI entry point (registered in pyproject.toml)
levels init-db                       # Create tables, seed states/sources, stamp migrations
levels migrate                       # Apply any pending data/db/migrations/*.sql files
levels pipeline                      # fetch → fetch-usgs-ogc → calc-rating → update-gauge-cache → calculator → build → orphan-check → check-reaches
levels build                         # Generate static HTML/CSV/text to public_html/

# Less-common subcommands (see `levels <cmd> --help` for details)
levels fetch                         # One shot of the pipeline's first stage
levels fetch-usgs-ogc                # Fetch USGS OGC continuous data for gauges with usgs_id
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

### PHP Tooling

```bash
composer install --no-interaction --no-progress --prefer-dist  # First-time setup

composer test                          # vendor/bin/phpunit
composer analyse                       # vendor/bin/phpstan --memory-limit=1G (level 8)
composer fix                           # vendor/bin/php-cs-fixer fix (in-place)
composer fix-check                     # ... --dry-run (what CI runs)
composer baseline                      # Regenerate phpstan-baseline.neon
```

PHPStan runs at **level 8** with `phpstan-baseline.neon` carrying pre-existing
`PDOStatement|false`/`string|false`-narrowing finds (see file header for the
shrinkage history through Tiers 2–6 of `docs/PLAN_php_layer_split.md`).
PHP-FPM in prod **lacks mbstring** — use `strlen`/`substr`/`strtolower`,
not `mb_*`. CSP is enforced — `<script>` tags must have `src=`; no inline
event handlers.

Integration tests use `tests/php/IntegrationTestCase.php`, which spawns
`php -S 127.0.0.1:0` against a tmp SQLite DB seeded by `levels init-db`
plus a per-test-class `seedDatabase()` hook. For editor-gated endpoints,
`seedEditorSession($email, $status = 'full'|'maintainer')` returns
`{editor_id, session_token, csrf_token}` — pass through `request()`'s
`$cookies` arg as `ed_sess` + `ed_csrf`, plus `csrf_token` in the POST
body for double-submit CSRF.

### PHP Conventions (`php/includes/`)

Moved to [`php/CONVENTIONS.md`](php/CONVENTIONS.md). The runtime
constraints (mbstring, CSP) and integration-test scaffold remain in
§ "PHP Tooling" above.

### Stream Tracing

```bash
bash scripts/extract_trace_data.sh       # One-time: pre-extract HUC4 GDBs → fast GPKGs (~20 min)
levels trace --putin LAT,LON \           # Trace a reach between put-in and take-out
    --takeout LAT,LON --name "River Name"
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
7. **orphan-check** — soft-fails the run (after build) if any fetch-active source lacks a `gauge_source` link; the existing systemd `OnFailure` chain emails + ntfys on the non-zero exit. See `docs/done/PLAN_orphan_sources.md`.
8. **check-reaches** — soft-fails the run (after build) if any `reach.geom` fails the format / endpoint validator (`kayak.cli.check_reaches.scan_for_issues`); raises so the same `OnFailure` chain fires.

Multi-source gauges aggregate across all linked sources directly: `update-gauge-cache` reads MAX across `gauge_source`, and PHP queries JOIN through `gauge_source` rather than picking a primary source.

### Two-Layer Web Architecture

**Python (static generation):** `levels build` writes self-contained HTML pages to `public_html/` with inlined CSS (from `src/kayak/web/static/style.css`) and SVG sparklines. These are the main river levels tables.

**PHP (dynamic pages):** PHP files in `php/` handle interactive features — description pages with plots, data APIs, editing, the reach picker, and source/gauge browsers. Both layers share the same database (`SQLITE_PATH` env var for PHP, `DATABASE_URL` for Python).

### Database

Single normalized SQLite database (`kayak.db`). Schema defined in `src/kayak/db/models.py` (SQLAlchemy 2.x ORM, 24 tables; live DB adds `schema_migrations` for 25 total). Key tables:

- `source` / `gauge` / `gauge_source` — data sources and physical gauge stations. `source.timezone` is an IANA TZ name (populated from `sources.yaml` → `stations:`) used by `BaseParser.dump_to_db` to localize naive timestamps from feeds that publish local time (USBR's per-station local TZ; wa.gov PST year-round). NULL = treat naive as UTC.
- `observation` — time-series data (source_id, observed_at, data_type, value)
- `latest_observation` / `latest_gauge_observation` — cached most-recent reading with delta_per_hour
- `reach` / `reach_state` / `reach_class` / `reach_guidebook` — paddleable runs with state, class, and guidebook relationships
- `fetch_url` / `calc_expression` — how to obtain data (fetch vs. calculate)
- `rating` / `rating_data` — gage height ↔ flow conversion tables (dormant — reserved for per-gauge rating curves)
- `editor` / `editor_session` / `editor_magic_link` — Phase 1 editor accounts + session cookies
- `change_request` / `change_request_attachment` / `edit_history` — proposal queue + audit trail
- `huc_name` — WBD HUC2/4/6/8/10/12 name lookup populated by `levels assign-huc`
- `schema_migrations` — tracks applied `data/db/migrations/*.sql` versions

Schema evolution:
1. Add or change the model in `models.py`.
2. For new fresh-DB shape only, `levels init-db` (re)creates tables via `Base.metadata.create_all()` and stamps every discovered migration file as applied.
3. For changes that need to land on an existing DB (ALTER / DROP / rename / CHECK), add a new `data/db/migrations/NNNN_description.sql` and run `levels migrate` — SQL runs in file-order inside a transaction; the row in `schema_migrations` records completion.
4. Migrations that delete `source` rows have a checklist — see [`docs/migrations.md`](docs/migrations.md) for the orphan-prevention pre-flight (calc-input verification, fetch_url cleanup, `levels orphan-check` against a sandbox).
5. **`reach.geom` is the documented exception to "reach changes go via a migration."** Geometry is not migration-managed: it's snapshotted to `data/db/reaches.json` (excluded from `reach.csv`; not regenerable on prod without the dev-only DEM/NHD trace stack) and applied with `python scripts/import_metadata.py --geom-only`. After a dev re-trace, run `scripts/export_metadata.py` and commit `reaches.json`; `scripts/deploy.sh` applies it on prod automatically. See [`deploy/SETUP.md`](deploy/SETUP.md) § 4.

### Parser System

Parsers inherit from `BaseParser` (in `src/kayak/parsers/base.py`) and register via `@register("name")` decorator. Each parser implements `parse_records(text) -> list[ObservationRecord]` (abstract, pure — no DB); `BaseParser.parse(text)` wraps it with the `dump_to_db` + buffer-flush path. Only override `parse()` to emit a syntax-error log line (see `nwps`, `usace_cda`, `nwrfc_xml`). `ensure_all_loaded()` imports all parser modules to trigger registration. Parser names match entries in `data/sources.yaml`.

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

