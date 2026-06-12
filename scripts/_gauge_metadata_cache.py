"""Shared gauge metadata cache path resolution for maintenance scripts."""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_CACHE = Path(__file__).resolve().parents[1] / "Gauge-metadata-cache" / "gauges.db"


DEFAULT_GAUGE_METADATA_CACHE = Path(os.environ.get("GAUGE_METADATA_CACHE", str(_DEFAULT_CACHE)))
