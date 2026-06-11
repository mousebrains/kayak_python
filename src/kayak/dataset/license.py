"""Dataset-owned data-license presentation.

The dataset contract already requires ``dataset.yaml: license``. This module
turns that stable identifier into the label/URL shape consumed by public
footers, emitted PHP runtime config, and downloadable JSON metadata. Unknown
license identifiers stay valid and render as their literal dataset label; known
Creative Commons identifiers get canonical labels and URLs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from kayak.dataset.contract import DATASET_YAML, load_dataset_meta


@dataclass(frozen=True)
class DataLicense:
    """Resolved public data-license metadata."""

    identifier: str
    label: str
    url: str
    notice: str

    def as_config(self) -> dict[str, str]:
        """Runtime-config shape for PHP consumers."""
        return {
            "id": self.identifier,
            "label": self.label,
            "url": self.url,
            "notice": self.notice,
        }

    def as_json_meta(self, *, attribution: str) -> dict[str, str]:
        """Top-level ``_meta`` shape for downloadable JSON outputs."""
        return {
            "license": self.label,
            "license_url": self.url,
            "attribution": attribution,
            "notice": self.notice,
        }


_KNOWN_LICENSES: dict[str, tuple[str, str]] = {
    "CC-BY-NC-4.0": ("CC BY-NC 4.0", "https://creativecommons.org/licenses/by-nc/4.0/"),
    "CC-BY-4.0": ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"),
    "CC0-1.0": ("CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/"),
}

_DEFAULT_IDENTIFIER = "CC-BY-NC-4.0"


def _license_key(value: str) -> str:
    return re.sub(r"[\s_]+", "-", value.strip().upper())


def _resolve_license(value: str) -> DataLicense:
    raw = value.strip()
    key = _license_key(raw)
    label, url = _KNOWN_LICENSES.get(key, (raw, ""))
    return DataLicense(
        identifier=raw,
        label=label,
        url=url,
        notice=(f"Metadata + calculated values: {label}. Observations: public domain at source."),
    )


def load_data_license(dataset_dir: Path) -> DataLicense:
    """Resolve the public data license for *dataset_dir*.

    A missing ``dataset.yaml`` uses the packaged generic fallback license, which
    keeps legacy/scaffold builds aligned with the served fallback
    ``LICENSE-DATA.txt``. A present manifest must carry a non-empty string
    ``license`` value; malformed manifests raise from the contract loader.
    """
    meta = load_dataset_meta(dataset_dir)
    if meta is None:
        return _resolve_license(_DEFAULT_IDENTIFIER)
    raw: Any = meta.get("license")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{DATASET_YAML}: license must be a non-empty string")
    return _resolve_license(raw)


@lru_cache(maxsize=1)
def get_data_license() -> DataLicense:
    """Cached data-license metadata for the configured dataset root."""
    from kayak.config import DATASET_DIR

    return load_data_license(DATASET_DIR)
