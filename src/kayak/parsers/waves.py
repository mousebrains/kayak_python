"""Waves parser (replaces Parse_Waves.C).

Simple tab-delimited ocean wave height data.
Converts cm to feet.
"""

from __future__ import annotations

import logging
import math

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import cm_to_feet, parse_datetime, safe_float

logger = logging.getLogger(__name__)


@register("ocean.newport")
class WavesParser(BaseParser):
    name = "ocean.newport"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Station from URL tail, uppercased
        url_path = self.url.rstrip("/")
        parts = url_path.split("/")
        self._station = parts[-1].split(".")[0].upper() if parts else "WAVES"

    def parse_line(self, line: str) -> bool:
        if not line.strip() or line.startswith("#"):
            return True

        parts = line.split("\t")
        if len(parts) < 2:
            parts = line.split()
        if len(parts) < 2:
            return True

        when = parse_datetime(parts[0])
        if when is None and len(parts) >= 3:
            when = parse_datetime(parts[0] + " " + parts[1])
            val_idx = 2
        else:
            val_idx = 1

        if when is None:
            return True

        if val_idx < len(parts):
            raw = safe_float(parts[val_idx])
            if raw is not None and math.isfinite(raw):
                # Convert cm to feet, round to 0.1
                feet = cm_to_feet(raw)
                self.dump_to_db(self._station, DataType.GAGE, when, feet)

        return True
