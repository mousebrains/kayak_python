# Contributing to Kayak

## Development Setup

```bash
# Clone and install
git clone <repo-url>
cd kayak
python3 -m venv /path/to/venv
/path/to/venv/bin/pip install -e ".[dev]"

# Initialize database
levels init-db

# Verify everything works
make check
```

## Code Quality

All code must pass lint, type checking, and tests before merge.

```bash
make lint       # ruff check src/ tests/
make typecheck  # mypy src/
make test       # pytest
make check      # all three
```

**Ruff** is configured in `pyproject.toml`: Python 3.13 target, 100-char line
length, rules `E W F I UP B SIM RUF`. Auto-fix with `make format`.

**mypy** runs in strict mode on `src/`.

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
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register

@register("your_parser")
class YourParser(BaseParser):
    """Description of the data source and format."""

    name = "your_parser"

    def parse_line(self, line: str) -> bool:
        # Parse one line, call self.dump_to_db() for each observation
        # Return True to continue, False to stop
        ...
        return True
```

2. Add the source URLs to `data/sources.yaml`:

```yaml
your_parser:
  urls:
    - url: "https://api.example.gov/data?station=XYZ"
```

3. Run `levels init-db` to register the new fetch URLs.

4. Add tests in `tests/test_parsers/test_your_parser.py` using sample data
   from the API.

## Adding a New Data Source (Gauge)

See `scripts/` for helper scripts. The typical workflow:

1. Find the gauge in USGS/NWPS metadata
2. Create the gauge and source rows in the database
3. Link them via `gauge_source`
4. Add the fetch URL to `data/sources.yaml`
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
