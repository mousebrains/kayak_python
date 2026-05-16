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
     fetch → fetch-usgs-ogc → calc-rating → update-gauge-cache → calculator → build
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
# 1. Install into a venv (one-time)
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# 2. Activate it — every subsequent step assumes `levels` resolves to .venv/bin/levels.
source .venv/bin/activate

# 3. Initialize database (creates tables, seeds states/sources from YAML)
levels init-db

# 4. Run the full pipeline (fetch live data, generate HTML)
levels pipeline

# 5. Serve locally
php -S localhost:8000 -t public_html
```

Prefer fully-qualified paths over `source .venv/bin/activate` if your
shell config makes activation noisy: replace every `levels …` with
`/path/to/.venv/bin/levels …`. Production runs that way — see
`deploy/SETUP.md` for the prod layout.

## CLI Commands

| Command | Purpose |
|---------|---------|
| `levels init-db` | Create tables, seed states/sources from `data/sources.yaml`, stamp all known migrations |
| `levels migrate` | Apply pending `data/db/migrations/*.sql` files (tracked in `schema_migrations`) |
| `levels pipeline` | Run full pipeline: fetch → fetch-usgs-ogc → calc-rating → update-gauge-cache → calculator → build |
| `levels fetch` | Fetch observations from all active sources (standalone — also runs as pipeline stage 1) |
| `levels fetch-usgs-ogc` | Fetch USGS continuous data via the OGC API for gauges with `usgs_id` |
| `levels calc-rating` | Interpolate missing flow/gage values using rating tables |
| `levels calculator` | Evaluate calculated expressions (synthetic gauges) |
| `levels build` | Generate static HTML/CSV/text to `public_html/` |
| `levels decimate` | Thin old observations (keeps 90d full, 1h/365d, 6h/archive) |
| `levels seed-maintainer --email …` | Create or promote an editor row to status=maintainer |
| `levels trace --putin … --takeout …` | Trace a reach along NHD HR flowlines |
| `levels assign-huc` | Assign HUC12 codes to reaches via WBD polygons (requires `[geo]` extra) |

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
| `/edit.php?id=N` | Reach editor (maintainer-only, editor-session cookie auth) |
| `/custom.php` | Custom levels page builder |

## Development

```bash
# Testing
pytest                            # Run all tests (in-memory SQLite)
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
- **kayak-healthcheck** — hourly at :45 (data-freshness check, emails on staleness)
- **kayak-backup-hourly** — every hour at :38 (sqlite `.backup` + WAL checkpoint; 24-copy retention; RPO ≤ 1h)
- **kayak-decimate** — daily at 02:32 (thin old observations)
- **kayak-cert-expiry** — daily at 06:30 (Let's Encrypt cert health probe; pages on <21 days remaining)
- **kayak-editor-retention** — daily at 03:45 (prune expired editor sessions + magic links)
- **kayak-metadata-snapshot** — daily at 04:30 (commit metadata-table drift to `data/db/*.csv`)
- **kayak-cert-renewal-test** — weekly Monday 04:15 (`certbot renew --dry-run`)
- **kayak-backup-weekly** — weekly Sunday 03:15 (4-copy retention; chains to off-site upload via `OnSuccess=`)
- **kayak-audit-gauges** — weekly Sunday 03:29 (orphan-gauge + reach-mapping audit, emails on drift)
- **kayak-config-drift** — weekly Sunday 05:30 (diffs repo `conf/`/`deploy/`/`systemd/` against `/etc/`, alerts on drift)
- **kayak-heartbeat** — weekly Sunday 06:00 (confirms alert pipeline)

## Documentation

| Document | Contents |
|----------|----------|
| [CLAUDE.md](CLAUDE.md) | Architecture, dev setup, conventions, key patterns |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development workflow, testing, adding parsers |
| [deploy/SETUP.md](deploy/SETUP.md) | Production deployment (Hetzner/Oracle Cloud) |
| [docs/database-schema.md](docs/database-schema.md) | Full schema reference (25 ORM tables + `schema_migrations`) |
| [docs/schema-overview.svg](docs/schema-overview.svg) | ER diagram |
| [docs/nginx-hardening.md](docs/nginx-hardening.md) | Security hardening guide |

## Licensing

The project ships under four complementary licenses, reflecting the
different origins and curatorial labor of each layer:

| Layer | License |
|---|---|
| Code (Python + PHP) | [GPL v3 or later](LICENSE) |
| Database metadata | [CC BY-NC 4.0](LICENSE-DATA) |
| Calculated gauge series | [CC BY-NC 4.0](LICENSE-DATA) |
| Observation time-series | Public domain at source (USGS, NOAA, USACE, USBR, IDWR, state agencies) |

See [LICENSE](LICENSE) for the full code-license text and [LICENSE-DATA](LICENSE-DATA) for the full data-license terms.
