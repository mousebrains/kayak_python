.PHONY: lint format typecheck test coverage check pipeline build clean

VENV ?= /home/pat/.venv
PYTHON = $(VENV)/bin/python
PIP = $(VENV)/bin/pip

## Development

lint:  ## Run linter
	$(VENV)/bin/ruff check src/ tests/

format:  ## Auto-format code
	$(VENV)/bin/ruff format src/ tests/

typecheck:  ## Run type checker
	$(VENV)/bin/mypy src/

test:  ## Run tests
	$(VENV)/bin/pytest -q

coverage:  ## Run tests with coverage report
	$(VENV)/bin/pytest --cov=kayak --cov-report=term-missing -q

check: lint typecheck test  ## Run all checks (lint + typecheck + test)

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
