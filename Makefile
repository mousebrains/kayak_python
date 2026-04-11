.PHONY: lint format typecheck test coverage check pipeline build clean \
       lint-php lint-js lint-css lint-shell lint-all

VENV ?= /home/pat/.venv
PYTHON = $(VENV)/bin/python
PIP = $(VENV)/bin/pip

## Development

lint:  ## Run Python linter
	$(VENV)/bin/ruff check src/ tests/

format:  ## Auto-format Python code
	$(VENV)/bin/ruff format src/ tests/

typecheck:  ## Run type checker
	$(VENV)/bin/mypy src/

test:  ## Run tests
	$(VENV)/bin/pytest -q

coverage:  ## Run tests with coverage report
	$(VENV)/bin/pytest --cov=kayak --cov-report=term-missing -q

lint-php:  ## Syntax-check PHP files
	@for f in php/*.php php/includes/*.php; do php -l "$$f" || exit 1; done

lint-js:  ## Lint JavaScript files
	biome check static/ public_html/no_show_review.js src/kayak/web/static/levels.js

lint-css:  ## Lint CSS files
	biome check php/style.css src/kayak/web/static/style.css

lint-shell:  ## Lint shell scripts
	shellcheck --severity=warning scripts/*.sh systemd/*.sh hardening/*.sh

lint-all: lint lint-php lint-js lint-css lint-shell  ## Run all linters

check: lint-all typecheck test  ## Run all checks

## Application

pipeline:  ## Run full data pipeline
	$(VENV)/bin/levels pipeline

build:  ## Generate static HTML/CSV/text
	$(VENV)/bin/levels build

init-db:  ## Create/update database schema and seed data
	$(VENV)/bin/levels init-db

## Setup

install:  ## Install package in editable mode with dev dependencies
	$(PIP) install -e ".[dev]"

clean:  ## Remove build artifacts and caches
	rm -rf .mypy_cache .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
