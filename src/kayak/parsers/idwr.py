"""Idaho Department of Water Resources parser (replaces Parse_IDWR.C).

Format: Fixed-width with equal sign column markers.
States: 0=Wait for header, 1=Parse column positions, 2+=Data rows

Type mapping: GD/GH=GAGE, Q/QD/QT=FLOW
Water year adjustments for date parsing.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import parse_datetime, safe_float

logger = logging.getLogger(__name__)

_TYPE_MAP = {
    "GD": DataType.gauge,
    "GH": DataType.gauge,
    "GH/Q": DataType.gauge,
    "Q": DataType.flow,
    "QD": DataType.flow,
    "QT": DataType.flow,
}


@register("idwr")
class IDWRParser(BaseParser):
    name = "idwr"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._state = 0
        self._columns: list[int] = []  # Column boundary positions
        self._times: list[str] = []  # Date strings from header

    def parse_line(self, line: str) -> bool:
        if not line.strip():
            self._state = 0
            return True

        parts = line.split()
        if len(parts) < 3:
            self._state = 0
            return True

        if self._state == 0:
            # Look for header: "WY Station Parameter [dates...]"
            if parts[0] == "WY" and parts[1] == "Station" and parts[2] == "Parameter":
                self._state = 1
                self._times = []
                # Dates start at index 4, two fields per date
                i = 4
                while i < len(parts):
                    if i + 1 < len(parts):
                        self._times.append(parts[i - 1] + " " + parts[i])
                    i += 2
            return True

        if self._state == 1:
            # Column positions from equal-sign markers
            self._columns = []
            pos = 0
            while True:
                idx = line.find("=", pos)
                if idx < 0:
                    break
                # Find end of equal signs
                end = idx
                while end < len(line) and line[end] == "=":
                    end += 1
                # Next non-equal position is a boundary
                if end < len(line):
                    self._columns.append(end)
                pos = end

            self._state = 2 if len(self._columns) > 3 else 0
            return True

        # State 2+: Data rows
        now = datetime.now(timezone.utc)
        water_year = parts[0]
        station = parts[1]
        type_str = parts[2]

        data_type = _TYPE_MAP.get(type_str)
        if data_type is None:
            logger.error("Unrecognized IDWR type: %s", type_str)
            return True

        times_offset = 3
        for i in range(times_offset, len(self._columns)):
            time_idx = i - times_offset
            if time_idx >= len(self._times):
                break

            field = self._get_field(line, i)
            if not field or " " in field:
                continue

            val = safe_float(field)
            if val is None or not math.isfinite(val):
                continue

            time_str = water_year + " " + self._times[time_idx] + " 12:00"
            when = parse_datetime(time_str)
            if not when:
                continue

            # Water year adjustment: if in future, might need to back up
            if when > now:
                try:
                    prev_year = str(int(water_year) - 1)
                    time_str2 = prev_year + " " + self._times[time_idx] + " 12:00"
                    when2 = parse_datetime(time_str2)
                    if when2:
                        when = when2
                except ValueError:
                    continue

            self.dump_to_db(station, data_type, when, val)

        return True

    def _get_field(self, line: str, col_idx: int) -> str:
        """Extract a field from a fixed-width line using column boundaries."""
        if not self._columns or not line:
            return ""

        if col_idx > 0 and col_idx - 1 < len(self._columns):
            start = self._columns[col_idx - 1] + 1 if col_idx > 0 else 0
        else:
            start = 0

        if col_idx < len(self._columns):
            end = self._columns[col_idx]
        else:
            end = len(line)

        if start < len(line):
            return line[start:end].strip()
        return ""
