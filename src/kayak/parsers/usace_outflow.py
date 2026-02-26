"""USACE Outflow parser (replaces Parse_USACE_Outflow.C).

Format: Space-delimited with PROJECT identifier.
States: 0=Find "PROJECT-", 1=Find "REPORT" date, 2=Parse hour:value data
Flow values are in thousands (multiplied by 1000).
"""

from __future__ import annotations

import logging
import math

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import parse_datetime, safe_float, safe_int

logger = logging.getLogger(__name__)


@register("usace.outflow")
class USACEOutflowParser(BaseParser):
    name = "usace.outflow"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._state = 0
        self._project = ""
        self._date = ""

    def parse_line(self, line: str) -> bool:
        if self._state == 0:
            parts = line.split()
            try:
                idx = parts.index("PROJECT-")
            except ValueError:
                # Also try as a prefix
                for i, p in enumerate(parts):
                    if p.startswith("PROJECT-"):
                        self._project = p[len("PROJECT-"):]
                        self._state = 1
                        return True
                return True
            if idx + 1 < len(parts):
                self._project = parts[idx + 1]
                self._state = 1
            return True

        if self._state == 1:
            if "REPORT" in line:
                idx = line.find("REPORT")
                date_str = line[idx + len("REPORT"):].strip()
                # Collapse multiple spaces
                import re
                self._date = re.sub(r"\s+", " ", date_str).strip()
                self._state = 2
            else:
                self._state = 0
            return True

        if self._state == 2:
            parts = line.split()
            if len(parts) >= 3:
                hour = safe_int(parts[0])
                if hour is not None and hour > 0:
                    val = safe_float(parts[2])
                    if val is not None and math.isfinite(val):
                        time_str = self._date + " " + parts[0] + ":00"
                        when = parse_datetime(time_str)
                        if when:
                            # Values are in thousands
                            self.dump_to_db(
                                self._project, DataType.FLOW,
                                when, val * 1000,
                            )
                        else:
                            logger.error("Cannot parse date: %s", time_str)
            return True

        return True
