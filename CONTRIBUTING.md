# Contributing to Kayak

## Development Setup

```bash
# Clone and install
git clone <repo-url>
cd kayak
python3 -m venv /path/to/venv
/path/to/venv/bin/pip install -e ".[dev]"

# Initialize database: empty schema + canonical metadata (gauges, reaches,
# sources). A bare `levels init-db` seeds duplicate sources and leaves every
# source an orphan — see CLAUDE.md § Quick start.
levels init-db --no-seed
python scripts/import_metadata.py

# Verify everything works (the test suite uses in-memory SQLite — the DB
# above is only needed to run `levels pipeline` or serve the site).
make check
```

## Code Quality

All code must pass lint, type checking, and tests before merge.

```bash
make lint       # ruff check + ruff format --check (src/ tests/ scripts/ docs/one-offs/)
make typecheck  # mypy src/ + the gated metadata/elevation scripts
make test       # pytest
make check      # lint-all (Python + PHP/JS/CSS/shell) + typecheck + test
```

**Ruff** is configured in `pyproject.toml`: Python 3.13 target, 100-char line
length, rules `E W F I UP B SIM RUF C901`. Auto-fix with `make format`.

**mypy** runs in strict mode on `src/` plus the gated prod-path scripts
(metadata import/export, elevation refresh).

**Biome** lints JS/CSS (`make lint-js` / `make lint-css`) and runs as part of
`make check` via `lint-all`.

## Testing

Tests use an in-memory SQLite database with transactional rollback, so no disk
I/O or external dependencies are needed.

```bash
pytest                            # Run all tests
pytest tests/test_parsers/        # Run parser tests only
pytest -k test_store_observation  # Run a single test by name
make coverage                     # Run with coverage report
```

Fixtures are defined in `tests/conftest.py` and provide `engine`, `session`,
`sample_source`, `sample_gauge`, `sample_reach`, and `linked_source_gauge`.

When adding a new feature, add tests that cover:
- The happy path
- Edge cases (empty input, missing data, invalid values)
- Error conditions

## Adding a New Parser

1. Create `src/kayak/parsers/your_parser.py`:

```python
from kayak.parsers.base import BaseParser, ObservationRecord
from kayak.parsers.registry import register

@register("your_parser")
class YourParser(BaseParser):
    """Description of the data source and format."""

    name = "your_parser"

    def parse_records(self, text: str) -> list[ObservationRecord]:
        # Pure: text → records, no DB, no session.
        # Build and return ObservationRecord(station, data_type, observed_at, value)
        # for each observation found in text. Return [] on malformed input.
        records: list[ObservationRecord] = []
        # ... parsing logic ...
        return records
```

The inherited `BaseParser.parse(text)` wraps `parse_records` with the
`dump_to_db` + buffer-flush + "no updates" warning path; only override
it when you need to emit a syntax-error log line that `parse_records`
can't (see `nwps.parse`, `usace_cda.parse`, `nwrfc_xml.parse` for
examples — each re-validates the body and emits ERROR before
delegating to `super().parse(text)`).

2. Add the source URLs to `src/kayak/data/sources.yaml`:

```yaml
your_parser:
  urls:
    - url: "https://api.example.gov/data?station=XYZ"
```

3. Run `levels init-db` to register the new fetch URLs.

4. Add tests in `tests/test_parsers/`:
   - `test_your_parser_records.py` — session-free unit tests for
     `parse_records`. No DB fixture needed; assert against the
     returned `list[ObservationRecord]`.
   - `test_your_parser.py` — end-to-end tests of `parse()` against a
     fresh session (uses the `session` fixture). Pin a sample
     payload from the live API.

## Adding a New Data Source (Gauge)

See `scripts/` for helper scripts. The typical workflow:

1. Find the gauge in USGS/NWPS metadata
2. Create the gauge and source rows in the database
3. Link them via `gauge_source`
4. Add the fetch URL to `src/kayak/data/sources.yaml`
5. Run `levels init-db` and `levels pipeline`

## PHP code style

Conventions for files under `php/` and `php/includes/` (file shape,
naming, helper prefixes, module constants) live in
[`php/CONVENTIONS.md`](php/CONVENTIONS.md). Runtime constraints
(mbstring, CSP), tooling commands (composer, PHPStan, PHPUnit,
php-cs-fixer), and the integration-test scaffold are documented in
[`CLAUDE.md`](CLAUDE.md) § "PHP Tooling".

## Project Structure

```
src/kayak/
  cli/         CLI commands (one module per subcommand)
  db/          SQLAlchemy models and query helpers
  parsers/     Data source parsers (one per agency format)
  utils/       Conversions, HTTP client, algorithms
  web/         Static assets (CSS)
tests/         Mirrors src/ structure
data/          YAML config (sources, builder columns, descriptions)
php/           PHP web layer (dynamic pages, API endpoints)
scripts/       Data import, migration, and maintenance scripts
systemd/       Service and timer unit files
deploy/        Deployment configs (nginx, setup guide)
docs/          Schema docs and diagrams
```

## Commit Messages

Write concise commit messages that focus on *why* rather than *what*. Use
imperative mood (e.g., "Add USBR temperature parsing" not "Added...").

## Pull Requests

- Keep PRs focused on a single change
- Ensure `make check` passes
- Include test coverage for new functionality
- Update documentation if adding new commands or changing behavior
