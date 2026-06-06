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
| Metadata repo (`METADATA_DIR`) | `/home/pat/kayak_data` (separate clone — the CSVs + `reaches*.json`) |

`config.py` checks `~/.config/kayak/.env` before falling back to the default `load_dotenv()` search. PHP reads `/etc/kayak/runtime-config.json` (or `$KAYAK_CONFIG_PATH`), the JSON snapshot written by `levels emit-config` — a missing/unreadable file is a hard HTTP 500 (`[CONFIG-FATAL]`). The `SQLITE_PATH` env var is only a fallback when the JSON lacks `database_path`.

**Metadata repo (data-repo split):** the metadata CSVs + `reaches*.json` live in a **separate** repo, `kayak_data`, cloned alongside the code repo and located via the `METADATA_DIR` env var. The default *value* stays `data/db` (path resolution unchanged), but the CSVs no longer live in the code repo — clone `kayak_data` and point `METADATA_DIR` at it. Only `data/db/migrations/` (schema) stays in the code repo. `levels sync-metadata` / `import_metadata` / `export_metadata` all read/write `METADATA_DIR`. Humans edit metadata via a PR to `kayak_data`; the prod host auto-snapshots editor-approved prod edits there nightly. So the code repo's `main` can be branch-protected without the snapshot needing to push to it. See [`deploy/SETUP.md`](deploy/SETUP.md).

**`OUTPUT_DIR` convention:** the live host sets `OUTPUT_DIR=/home/pat/public_html` (outside the repo), so `levels build` writes to the nginx docroot and never touches the repo tree. On a separate dev machine, set `OUTPUT_DIR=/home/<user>/public_html_dev` (or similar non-repo path) in `~/.config/kayak/.env` and serve with `KAYAK_CONFIG_PATH=… php -S localhost:8000 -t "$OUTPUT_DIR"` (see § Running the PHP Web Layer for the config step). The default (unset) writes back into the repo's `public_html/`, which clobbers tracked dev symlinks and drops stray artifacts under `static/`. See `.env.example` for the full rationale.

POSIX ACLs grant `www-data`: traverse on `/home/pat` (not `/home/pat/kayak`), read on the docroot `/home/pat/public_html` (a real dir outside the repo — `levels build` copies PHP/static in) and on the operator status cache `/home/pat/var/status.html` (relocated out of the repo by R2.6/#47, so `www-data` reads zero repo paths), read-write on `/home/pat/DB`.

### Quick start

```bash
python3 -m venv /home/pat/.venv
/home/pat/.venv/bin/pip install -e ".[dev]"
git clone git@github.com:mousebrains/kayak_data.git /home/pat/kayak_data  # the metadata snapshot
echo 'METADATA_DIR=/home/pat/kayak_data' >> ~/.config/kayak/.env          # point the code at it
/home/pat/.venv/bin/levels init-db --no-seed            # Empty schema + stamp migrations
/home/pat/.venv/bin/python scripts/import_metadata.py   # Load gauges/reaches/sources from METADATA_DIR
/home/pat/.venv/bin/levels pipeline                     # Fetch live data and generate HTML
```

`init-db` creates the schema and stamps migrations; `--no-seed` skips the
`sources.yaml` state/source seed so the canonical rows from the metadata CSVs
(`METADATA_DIR`, loaded by `import_metadata.py`) import without duplicate-by-name sources.
Without the metadata load every source is an orphan with no `gauge_source`
link, so `levels pipeline` fails at `orphan-check` and the site renders empty.
A plain `levels init-db` (seeded from `data/sources.yaml`) is enough for a
fetch-only smoke test.

### Working on the live host

`/home/pat/kayak` is the **live editable-install tree**: the venv imports
`kayak` directly from `src/`, so the running systemd pipeline and scheduled
jobs execute *whatever branch is checked out here, right now*. A
`git checkout <feature-branch>` in this tree is therefore an unannounced
deploy — and `scripts/snapshot_metadata.sh` refuses to run unless it's on
`main`.

So **keep `/home/pat/kayak` on `main` and do all branch/PR work in a git
worktree** (the venv never imports a worktree):

```bash
scripts/new-worktree.sh my-feature   # ~/kayak-worktrees/my-feature, off origin/main
cd ~/kayak-worktrees/my-feature      # edit, commit, push, open the PR here
```

Deploy by merging the PR and running `git pull` on `main` in the live tree —
never by leaving a feature branch checked out. Remove a finished worktree with
`git worktree remove <path>`.

Full rationale, the incidents that motivated this, a recovery runbook, and the
deferred "frozen install artifact" fix: [`docs/live-tree-workflow.md`](docs/live-tree-workflow.md).

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
levels fetch-usgs-ogc                # Fetch USGS OGC continuous data for gauges linked to a USGS source
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
ruff check src/ tests/ scripts/ docs/one-offs/        # Lint (matches CI scope)
ruff check --fix src/ tests/ scripts/ docs/one-offs/  # Lint with auto-fix
ruff format src/ tests/ scripts/ docs/one-offs/       # Format
mypy src/ scripts/import_metadata.py scripts/export_metadata.py scripts/refresh_reach_elevations.py  # Type check (CI scope)
```

Ruff config: Python 3.13 target, 100-char line length, rules `E W F I UP B SIM RUF C901`. Configured in `pyproject.toml`.

### Running the PHP Web Layer

```bash
# PHP pages 500 without a runtime config (php/includes/config.php is
# fatal-on-missing): emit one first and point KAYAK_CONFIG_PATH at it.
levels emit-config --out ~/.config/kayak/runtime-config.json
KAYAK_CONFIG_PATH=~/.config/kayak/runtime-config.json \
    php -S localhost:8000 -t public_html  # Serve PHP pages + static build output
```

### PHP Tooling

```bash
composer install --no-interaction --no-progress --prefer-dist  # First-time setup

composer test                          # vendor/bin/phpunit
composer analyse                       # vendor/bin/phpstan --memory-limit=1G (level 9 + strict-rules)
composer fix                           # vendor/bin/php-cs-fixer fix (in-place)
composer fix-check                     # ... --dry-run (what CI runs)
composer baseline                      # Regenerate phpstan-baseline.neon
```

PHPStan runs at **level 9** with the full `phpstan-strict-rules`, plus a
`phpstan-baseline.neon` carrying a shrinking set of residual `mixed`-typing
(PDO-row) finds (see `docs/done/PLAN_phpstan_level9_strict.md`).
PHP-FPM in prod **lacks mbstring** — use `strlen`/`substr`/`strtolower`,
not `mb_*`. CSP is enforced — `<script>` tags must have `src=`; no inline
event handlers.

PHP tests use two harnesses. **`tests/php/FunctionalTestCase.php`** runs handlers
**in-process** — pcov counts it, so it's the primary vehicle (it lifted coverage
to ~60%); prefer it for handler coverage. **`tests/php/IntegrationTestCase.php`**
spawns `php -S 127.0.0.1:0` against a tmp SQLite DB seeded by `levels init-db`
plus a per-test-class `seedDatabase()` hook (true end-to-end, uncounted by pcov).
For editor-gated endpoints,
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
2. **fetch-usgs-ogc** — fetches USGS data via the OGC API for gauges linked to a USGS source
3. **calc-rating** — interpolates missing flow from gage height (or vice versa) using `Rating`/`RatingData` tables
4. **update-gauge-cache** — recomputes gauge-level latest observation values
5. **calculator** — evaluates `CalcExpression` formulas referencing `LatestObservation` values
6. **build** — generates per-state HTML pages, CSV, and text files to `public_html/`; inlines CSS and SVG sparklines
7. **orphan-check** — soft-fails the run (after build) if any fetch-active source lacks a `gauge_source` link; the existing systemd `OnFailure` chain emails + ntfys on the non-zero exit. See `docs/done/PLAN_orphan_sources.md`.
8. **check-reaches** — soft-fails the run (after build) if any `reach.geom` fails the format / endpoint validator (`kayak.cli.check_reaches.scan_for_issues`); raises so the same `OnFailure` chain fires.

Multi-source gauges aggregate across all linked sources directly: `update-gauge-cache` reads MAX across `gauge_source`, and PHP queries JOIN through `gauge_source` rather than picking a primary source.

### Two-Layer Web Architecture

**Python (static generation):** `levels build` writes self-contained HTML pages to `public_html/` with inlined CSS (from `src/kayak/web/static/style.css`) and SVG sparklines. These are the main river levels tables.

**PHP (dynamic pages):** PHP files in `php/` handle interactive features — description pages with plots, data APIs, editing, the reach picker, and source/gauge browsers. Both layers share the same database (`database_path` from the runtime-config JSON for PHP, with `SQLITE_PATH` as env fallback; `DATABASE_URL` for Python).

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
- `huc_name` — WBD HUC6/HUC8 name lookup populated by `levels assign-huc` (10/12 trimmed, R6.2)
- `schema_migrations` — tracks applied `data/db/migrations/*.sql` versions

Evolution splits into two distinct flows — **schema** changes (table shape) go via a migration; **metadata** changes (row data) go via a CSV diff + sync.

**Schema changes** (ALTER / DROP / rename / CHECK / index):
1. Add or change the model in `models.py` (keep it in lockstep — same PR).
2. For a fresh-DB shape only, `levels init-db` (re)creates tables via `Base.metadata.create_all()` and stamps every discovered migration file as applied.
3. To land on an existing DB, add a `data/db/migrations/NNNN_description.sql` and run `levels migrate` — SQL runs in file-order inside a transaction; the `schema_migrations` row records completion. **Migrations are schema-only**: a new migration (number > 0074) may not `INSERT`/`UPDATE`/`DELETE` a metadata table — enforced by `tests/test_scripts/test_migrations_schema_only.py` (the wire-via-migration era ≤ 0074 is grandfathered immutable history).

**Metadata changes** (rows in `source` / `gauge` / `gauge_source` / `reach` / the junctions / `fetch_url` / `calc_expression` / …): edit the reviewed CSV in the **`kayak_data`** repo (`METADATA_DIR`) — **no migration**. A *new* row takes a stable id from `id_counters.csv` (bump `next_id`; ids only ever increment, never reuse — guarded by `levels validate-dataset`, run on the fixture in code CI and on the real dataset by kayak_data's CI). On deploy, `scripts/deploy.sh` step 3.1 runs `levels sync-metadata`, which applies the CSV diff to the live DB **by id** (INSERT new / UPDATE changed / DELETE removed) while **preserving observations** — a rename is an UPDATE, so a source's observations stay valid. Deletes are gated behind `--allow-deletes` (it prints the per-source observation-drop counts first). See [`docs/PLAN_add_gauges_reaches.md`](docs/PLAN_add_gauges_reaches.md) for the add / update / remove / split-a-reach runbooks and [`docs/migrations.md`](docs/migrations.md) for `orphan-check` triage (orphans can still arise from a CSV edit).

**`reach.geom` and `reach.gradient_profile` are excluded from `reach.csv`** — large, machine-generated, and not regenerable on prod (the dev-only DEM/NHD trace stack) — so each is snapshotted to its own JSON in `kayak_data` (`reaches.json` / `reaches-gradient.json`, review-3 R6.1) and applied with `scripts/import_metadata.py --geom-only` / `--gradient-only` (deploy.sh steps 3.25 / 3.26, reading `METADATA_DIR`), **not** by the CSV sync. `reach.huc` is tool-derived (`levels assign-huc`, a deterministic point-in-polygon over the WBD HUC12 layer — `kayak.huc.assign`) but a single code diffs cleanly, so it rides **in** `reach.csv` like any other column (no separate JSON). After a dev re-trace: run `scripts/export_metadata.py`, commit `reach.csv` + the two JSONs to `kayak_data`; `scripts/deploy.sh` applies them on prod automatically. See [`deploy/SETUP.md`](deploy/SETUP.md) § 4.

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
- **PHP DB connection:** `php/includes/db.php` resolves the DB path via `Config::str('database_path')` (runtime-config JSON), then the `SQLITE_PATH` env var; SQLite PDO only

