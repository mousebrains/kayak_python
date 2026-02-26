"""NOAA text parser (replaces Parse_NOAA.C).

Format: Free-form text with keyword parsing.
States: 0=Wait for "Observed Data:", 1=Find column marker ("|"), 2=Parse data
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timedelta, timezone

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import parse_datetime, safe_float

logger = logging.getLogger(__name__)


@register("noaa")
class NOAAParser(BaseParser):
    name = "noaa"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._state = 0
        self._station = ""
        self._has_flow = False
        self._has_gage = False
        self._flow_col = -1
        self._gage_col = -1

        # Extract station from URL — last 4-5 chars of filename root
        url_path = self.url.rstrip("/")
        parts = url_path.split("/")
        if parts:
            name = parts[-1].split(".")[0]
            self._station = name[-5:] if len(name) > 5 else name

    def parse_line(self, line: str) -> bool:
        if self._state == 0:
            if "Observed Data" in line:
                self._state = 1
            return True

        if self._state == 1:
            if "|" in line:
                # Parse column positions from header
                cols = [c.strip() for c in line.split("|")]
                for i, c in enumerate(cols):
                    cu = c.upper()
                    if "CFS" in cu:
                        self._has_flow = True
                        self._flow_col = i
                    if "FT" in cu:
                        self._has_gage = True
                        self._gage_col = i
                self._state = 2
            return True

        if self._state == 2:
            return self._parse_data(line)

        return True

    def _parse_data(self, line: str) -> bool:
        if not line.strip() or "|" not in line:
            return True

        cols = [c.strip() for c in line.split("|")]
        if len(cols) < 2:
            return True

        # First column is date/time
        time_str = cols[0].strip()
        when = parse_datetime(time_str)
        if when is None:
            return True

        if self._has_flow and self._flow_col < len(cols):
            val = safe_float(cols[self._flow_col])
            if val is not None and not math.isinf(val):
                self.dump_to_db(self._station, DataType.flow, when, val)

        if self._has_gage and self._gage_col < len(cols):
            val = safe_float(cols[self._gage_col])
            if val is not None and not math.isinf(val):
                self.dump_to_db(self._station, DataType.gauge, when, val)

        return True
