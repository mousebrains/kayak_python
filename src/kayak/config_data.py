"""YAML config file loaders for sources, builder columns, and description fields.

These replace the database-stored configuration tables (URLParse, BuilderColumn,
DescriptionField) with static YAML files under data/.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# data/ directory is at the project root, sibling of src/
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def _load_yaml(filename: str) -> dict[str, Any]:
    """Load and parse a YAML file from the data directory."""
    path = _DATA_DIR / filename
    with open(path) as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=1)
def load_sources() -> list[dict[str, str]]:
    """Load source URL/parser definitions from data/sources.yaml.

    Returns list of dicts with keys: parser, url, hours, group.
    """
    data = _load_yaml("sources.yaml")
    return data.get("sources", [])


@lru_cache(maxsize=1)
def load_builder_columns() -> list[dict[str, Any]]:
    """Load builder column definitions from data/builder.yaml.

    Returns list of dicts with keys: sort_key, use, type, field, length,
    name_text, name_html.
    """
    data = _load_yaml("builder.yaml")
    return data.get("columns", [])


@lru_cache(maxsize=1)
def load_description_fields() -> list[dict[str, Any]]:
    """Load description field definitions from data/descriptions.yaml.

    Returns list of dicts with keys: sort_key, column, type, prefix, suffix,
    and optionally info.
    """
    data = _load_yaml("descriptions.yaml")
    return data.get("fields", [])
