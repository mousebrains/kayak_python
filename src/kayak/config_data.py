"""YAML config file loaders for builder columns and description fields.

These replace the database-stored configuration tables (BuilderColumn,
DescriptionField) with static YAML files packaged under ``kayak/data/``.
(The former ``load_sources()``/``sources.yaml`` seed was removed by the
dataset-separation S1-cleanup; the dataset's source registry +
``levels generate-sources`` own source/fetch_url definitions.)
"""

from functools import lru_cache
from typing import Any

import yaml

from kayak.resources import resource_dir

# Packaged YAML defaults — ship inside the kayak package so they resolve in both
# an editable (src/kayak/data) and a wheel (site-packages/kayak/data) install.
_DATA_DIR = resource_dir("data")


def _load_yaml(filename: str) -> dict[str, Any]:
    """Load and parse a YAML file from the data directory."""
    path = _DATA_DIR / filename
    try:
        with open(path) as f:
            result: dict[str, Any] = yaml.safe_load(f)
            return result
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Required config file not found: {path}. "
            f"Ensure the 'data/' directory contains {filename}."
        ) from None
    except yaml.YAMLError as e:
        raise ValueError(f"Error parsing {path}: {e}") from e


@lru_cache(maxsize=1)
def load_builder_columns() -> list[dict[str, Any]]:
    """Load builder column definitions from src/kayak/data/builder.yaml.

    Returns list of dicts with keys: sort_key, use, type, field, length,
    name_text, name_html.
    """
    data = _load_yaml("builder.yaml")
    result: list[dict[str, Any]] = data.get("columns", [])
    return result


@lru_cache(maxsize=1)
def load_description_fields() -> list[dict[str, Any]]:
    """Load description field definitions from src/kayak/data/descriptions.yaml.

    Returns list of dicts with keys: sort_key, column, type, prefix, suffix,
    and optionally info.
    """
    data = _load_yaml("descriptions.yaml")
    result: list[dict[str, Any]] = data.get("fields", [])
    return result


@lru_cache(maxsize=1)
def load_http_concurrency_overrides() -> dict[str, int]:
    """Load per-host concurrency caps from src/kayak/data/http_concurrency.yaml.

    Returns a dict of {hostname: int}. Empty if the file is missing the
    ``overrides:`` key. Hosts not listed use the http_client default.
    """
    data = _load_yaml("http_concurrency.yaml")
    raw = data.get("overrides", {}) or {}
    return {str(host): int(limit) for host, limit in raw.items()}
