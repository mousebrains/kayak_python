"""CBRFC pipe-delimited parser (replaces Parse_CBRFC.C).

Format: Pipe-delimited data with current time parsing.
States: 0=Wait for time, 1=Header, 2=Data
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import parse_datetime, safe_float

logger = logging.getLogger(__name__)


@register("cbrfc")
class CBRFCParser(BaseParser):
    name = "cbrfc"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._state = 0
        self._current_time: datetime | None = None
        self._columns: dict[str, int] = {}  # field_name -> column_index

    def parse_line(self, line: str) -> bool:
        if not line.strip():
            return True

        if self._state == 0:
            # Look for current time line
            if "Current Time" in line or "current time" in line.lower():
                parts = line.split(":", 1)
                if len(parts) >= 2:
                    time_str = parts[1].strip()
                    self._current_time = parse_datetime(time_str)
                self._state = 1
            return True

        tokens = [t.strip() for t in line.split("|")]

        if self._state == 1:
            # Header line with column names
            if tokens:
                self._columns = {name: i for i, name in enumerate(tokens)}
                self._state = 2
            return True

        if self._state == 2:
            return self._parse_data_row(tokens)

        return True

    def _parse_data_row(self, tokens: list[str]) -> bool:
        if not tokens or len(tokens) < 3:
            return True

        # First column is station name
        station = tokens[0].strip().replace(" ", "_")
        if not station:
            return True

        for col_name, idx in self._columns.items():
            if idx >= len(tokens) or idx == 0:
                continue

            col_lower = col_name.strip().lower()
            val = safe_float(tokens[idx])
            if val is None or math.isinf(val):
                continue

            if "cfs" in col_lower or "flow" in col_lower:
                data_type = DataType.FLOW
            elif "ft" in col_lower or "stage" in col_lower:
                data_type = DataType.GAGE
            else:
                continue

            # Use time from the data or fall back to current_time
            time_col = self._columns.get("Time") or self._columns.get("DATE")
            when = self._current_time or datetime.now(timezone.utc)
            if time_col is not None and time_col < len(tokens):
                parsed = parse_datetime(tokens[time_col])
                if parsed:
                    when = parsed

            self.dump_to_db(station, data_type, when, val)

        return True
