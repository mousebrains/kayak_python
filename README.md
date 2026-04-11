# Kayak

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)

Real-time river level, flow, gage height, and temperature data aggregated from
government agencies (USGS, NOAA, USACE, USBR, IDWR) for the
[Willamette Kayak and Canoe Club](https://wkcc.org).

Live site: [levels.wkcc.org](https://levels.wkcc.org)

## Architecture

```
  USGS / NOAA / USACE / USBR / IDWR APIs
                 |
          levels pipeline          (Python — runs hourly via systemd)
     fetch → calc-rating → merge → calculator → build
                 |                               |
              SQLite DB                    public_html/
              (kayak.db)                  (static HTML/CSV)
                 |                               |
              PHP layer  <-------- nginx ------->+
         (dynamic pages,                   (static pages,
          plots, editing,                   per-state tables)
          API endpoints)
```

**Python pipeline** fetches data from government APIs, parses it through
source-specific parsers, stores observations in a normalized SQLite database,
and generates static HTML pages with inlined SVG sparklines.

**PHP web layer** handles interactive features — reach descriptions with
time-series plots, data browsing, the reach picker, and a reach editor.

Both layers share the same SQLite database. See
[docs/database-schema.md](docs/database-schema.md) for the full schema
([ER diagram](docs/schema-overview.svg)).

## Quick Start

```bash
# 1. Install
python3 -m venv /path/to/venv
/path/to/venv/bin/pip install -e ".[dev]"

# 2. Initialize database (creates tables, seeds states/sources from YAML)
levels init-db

# 3. Run the full pipeline (fetch live data, generate HTML)
levels pipeline

# 4. Serve locally
php -S localhost:8000 -t public_html
```

After `init-db`, gauges and reaches must be imported separately from a
production database dump. See [CLAUDE.md](CLAUDE.md) for the full import
workflow using `scripts/import_from_dump.py`.

## CLI Commands

| Command | Purpose |
|---------|---------|
| `levels init-db` | Create tables, seed states/sources from `data/sources.yaml` |
| `levels pipeline` | Run full pipeline: fetch + calc-rating + merge + calculator + build |
| `levels fetch` | Fetch observations from all active sources |
| `levels fetch-usgs-ogc` | Fetch USGS data via the OGC SensorThings API |
| `levels calc-rating` | Interpolate missing flow/gage values using rating tables |
| `levels merge` | Merge observations from multiple sources per gauge |
| `levels calculator` | Evaluate calculated expressions (synthetic gauges) |
| `levels build` | Generate static HTML/CSV/text to `public_html/` |
| `levels decimate` | Thin old observations (keeps 90d full, 1h/365d, 6h/archive) |

## PHP API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `/api.php?id=N&type=T&days=D` | JSON time-series data for a source |
| `/latest.php` | JSON latest observations for all gauges |
| `/description.php?id=N` | Reach detail page with plots and metadata |
| `/plot.php?id=N&type=T` | SVG time-series chart |
| `/reach.php` | Reach browser with navigation |
| `/gauge.php?id=N` | Gauge details and associated sources/reaches |
| `/source.php?id=N` | Source metadata and recent observations |
| `/data.php?id=N` | Raw observation data inspector |
| `/picker.php` | Interactive reach picker |
| `/edit.php?id=N` | Reach editor (HTTP Basic Auth) |
| `/custom.php` | Custom levels page builder |

## Development

```bash
# Testing
pytest                            # Run all 304 tests (in-memory SQLite)
pytest --cov=kayak                # With coverage report
pytest -k test_store_observation  # Run a single test

# Linting
ruff check src/ tests/            # Lint
ruff format src/ tests/           # Format
mypy src/                         # Type check

# All checks (via Makefile)
make check                        # lint + typecheck + test
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for development workflow and guidelines.

## Deployment

Production runs on Debian 13 with nginx + PHP-FPM + systemd timers.
See [deploy/SETUP.md](deploy/SETUP.md) for the full deployment guide.

Key systemd timers:
- **kayak-pipeline** — hourly at :12 (fetch + build)
- **kayak-decimate** — daily at 02:32 (thin old observations)
- **kayak-backup** — weekly Sunday 03:15 (4-copy retention)

## Documentation

| Document | Contents |
|----------|----------|
| [CLAUDE.md](CLAUDE.md) | Architecture, dev setup, conventions, key patterns |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development workflow, testing, adding parsers |
| [deploy/SETUP.md](deploy/SETUP.md) | Production deployment (Hetzner/Oracle Cloud) |
| [docs/database-schema.md](docs/database-schema.md) | Full schema reference (18 tables) |
| [docs/schema-overview.svg](docs/schema-overview.svg) | ER diagram |
| [docs/nginx-hardening.md](docs/nginx-hardening.md) | Security hardening guide |

## License

[GNU General Public License v3.0](LICENSE)
