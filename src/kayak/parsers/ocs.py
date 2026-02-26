"""OCS parser (replaces Parse_OCS.C).

NOAA/NWS format with complex header parsing.
States: 0=NWS check, 1=Date, 2=Header with column positions, 3=Data
"""

from __future__ import annotations

import logging
import math

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import kcfs_to_cfs, parse_datetime, safe_float

logger = logging.getLogger(__name__)


@register("ocs")
class OCSParser(BaseParser):
    name = "ocs"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._state = 0
        self._station = ""
        self._date = ""
        self._flow_pos = -1
        self._gage_pos = -1
        self._is_kcfs = False

    def parse_line(self, line: str) -> bool:
        if not line.strip():
            return True

        if self._state == 0:
            # Look for National Weather Service identifier
            if "National Weather Service" in line or "NWS" in line:
                self._state = 1
            # Extract station name
            parts = line.split()
            if parts and len(parts[0]) >= 3:
                if not any(c.isdigit() for c in parts[0][:3]):
                    self._station = parts[0].replace(" ", "_")
            return True

        if self._state == 1:
            # Look for date
            when = parse_datetime(line.strip())
            if when:
                self._date = line.strip()
                self._state = 2
            return True

        if self._state == 2:
            # Header — find FLOW/STAGE column positions
            upper = line.upper()
            parts = line.split()
            for i, p in enumerate(parts):
                pu = p.upper()
                if pu in ("FLOW", "CFS", "KCFS"):
                    self._flow_pos = i
                    self._is_kcfs = pu == "KCFS"
                elif pu in ("STAGE", "FT", "FEET"):
                    self._gage_pos = i

            if self._flow_pos >= 0 or self._gage_pos >= 0:
                self._state = 3
            return True

        if self._state == 3:
            return self._parse_data(line)

        return True

    def _parse_data(self, line: str) -> bool:
        parts = line.split()
        if len(parts) < 2:
            return True

        # First token(s) may be date/time
        time_str = parts[0]
        if len(parts) > 1 and ":" in parts[1]:
            time_str = parts[0] + " " + parts[1]

        when = parse_datetime(time_str)
        if when is None and self._date:
            when = parse_datetime(self._date + " " + parts[0])
        if when is None:
            return True

        if self._flow_pos >= 0 and self._flow_pos < len(parts):
            val = safe_float(parts[self._flow_pos])
            if val is not None and math.isfinite(val):
                if self._is_kcfs:
                    val = kcfs_to_cfs(val)
                if 0 <= val <= 2e6:
                    self.dump_to_db(self._station, DataType.flow, when, val)

        if self._gage_pos >= 0 and self._gage_pos < len(parts):
            val = safe_float(parts[self._gage_pos])
            if val is not None and math.isfinite(val):
                self.dump_to_db(self._station, DataType.gauge, when, val)

        return True
