"""NWRFC text parser (replaces Parse_NWRFC.C).

Format: Fixed-width text with (ft) and (cfs) column markers.
States: 0=Find column markers, 1+=Data rows
"""

from __future__ import annotations

import logging
import math

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import parse_datetime, safe_float

logger = logging.getLogger(__name__)


@register("nwrfc")
class NWRFCParser(BaseParser):
    name = "nwrfc"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._state = 0
        self._gage_col = -1
        self._flow_col = -1
        self._is_inflow = False

        # Check URL for inflow indicator
        if "inflow" in self.url.lower():
            self._is_inflow = True

    def parse_line(self, line: str) -> bool:
        if not line.strip():
            return True

        if self._state == 0:
            # Look for column markers: (ft) and (cfs)
            lower = line.lower()
            if "(ft)" in lower or "(cfs)" in lower:
                # Find positions of markers
                parts = line.split()
                for i, p in enumerate(parts):
                    pl = p.lower().strip("()")
                    if pl == "ft":
                        self._gage_col = i
                    elif pl == "cfs":
                        self._flow_col = i

                if self._gage_col >= 0 or self._flow_col >= 0:
                    self._state = 1
            return True

        # State 1+: Data rows
        parts = line.split()
        if len(parts) < 3:
            return True

        # First two tokens are date and time, then station, then data
        # Or: station date time value(s)
        # Try to find date/time in first tokens
        station = ""
        when = None

        # Try pattern: STATION MM/DD HH:MM value(s)
        if len(parts) >= 4:
            station = parts[0].strip()
            time_str = parts[1] + " " + parts[2]
            when = parse_datetime(time_str)

        if when is None:
            return True

        if self._gage_col >= 0 and self._gage_col < len(parts):
            val = safe_float(parts[self._gage_col])
            if val is not None and math.isfinite(val):
                self.dump_to_db(station, DataType.GAGE, when, val)

        if self._flow_col >= 0 and self._flow_col < len(parts):
            val = safe_float(parts[self._flow_col])
            if val is not None and math.isfinite(val) and val >= 0:
                dtype = DataType.INFLOW if self._is_inflow else DataType.FLOW
                self.dump_to_db(station, dtype, when, val)

        return True
