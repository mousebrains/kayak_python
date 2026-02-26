"""NOAA2 HTML-rendered parser (replaces Parse_NOAA2.C).

Similar to NOAA but uses HTML rendering (serveUpCookedLines).
Supports KCFS→CFS conversion.
"""

from __future__ import annotations

import logging
import math

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import kcfs_to_cfs, parse_datetime, safe_float

logger = logging.getLogger(__name__)


@register("noaa2")
class NOAA2Parser(BaseParser):
    name = "noaa2"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._state = 0
        self._station = ""
        self._timezone = ""
        self._flow_type: str | None = None  # "cfs" or "kcfs"
        self._has_gage = False

        # Extract station from URL
        url_path = self.url.rstrip("/")
        parts = url_path.split("/")
        if parts:
            name = parts[-1].split(".")[0]
            self._station = name[-5:] if len(name) > 5 else name

    def parse(self, text: str) -> int:
        # NOAA2 uses cooked (HTML-stripped) lines
        return self.parse_cooked(text)

    def parse_line(self, line: str) -> bool:
        if self._state == 0:
            # Wait for data marker
            if "Observed" in line or "observed" in line:
                self._state = 1
            return True

        if self._state == 1:
            # Find timezone
            upper = line.upper()
            for tz in ("PST", "PDT", "MST", "MDT", "CST", "CDT", "EST", "EDT", "UTC", "GMT"):
                if tz in upper:
                    self._timezone = tz
                    break
            if self._timezone:
                self._state = 2
            return True

        if self._state == 2:
            # Detect column types
            upper = line.upper()
            if "KCFS" in upper:
                self._flow_type = "kcfs"
            elif "CFS" in upper:
                self._flow_type = "cfs"
            if "FT" in upper:
                self._has_gage = True
            if self._flow_type or self._has_gage:
                self._state = 3
            return True

        if self._state >= 3:
            return self._parse_data(line)

        return True

    def _parse_data(self, line: str) -> bool:
        if not line.strip():
            return True

        parts = line.split()
        if len(parts) < 3:
            return True

        # Try to parse date from first two tokens
        time_str = parts[0] + " " + parts[1]
        when = parse_datetime(time_str, self._timezone)
        if when is None:
            return True

        # Remaining tokens are data values
        data_idx = 2
        if self._flow_type and data_idx < len(parts):
            val = safe_float(parts[data_idx])
            if val is not None and not math.isinf(val):
                if self._flow_type == "kcfs":
                    val = kcfs_to_cfs(val)
                self.dump_to_db(self._station, DataType.FLOW, when, val)
            data_idx += 1

        if self._has_gage and data_idx < len(parts):
            val = safe_float(parts[data_idx])
            if val is not None and not math.isinf(val):
                self.dump_to_db(self._station, DataType.GAGE, when, val)

        return True
