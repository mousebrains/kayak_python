"""USACE Outflow parser.

Format: Space-delimited with PROJECT identifier.
States: 0=Find "PROJECT-", 1=Find "REPORT" date, 2=Parse hour:value data
Flow values are in thousands (multiplied by 1000).
"""

import logging
import math
import re
from datetime import timedelta
from typing import Any

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import parse_datetime, safe_float, safe_int

logger = logging.getLogger(__name__)


@register("usace.outflow")
class USACEOutflowParser(BaseParser):
    """US Army Corps of Engineers outflow text report parser.

    Parses fixed-format text reports from USACE dam projects. Extracts
    outflow values in kcfs (converted to cfs) using a two-state machine:
    state 0 finds the project/date header, state 1 reads hourly outflow rows.
    """

    name = "usace.outflow"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
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
                for _i, p in enumerate(parts):
                    if p.startswith("PROJECT-"):
                        self._project = p[len("PROJECT-") :]
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
                date_str = line[idx + len("REPORT") :].strip()
                # Collapse multiple spaces
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
                        if hour == 24:
                            # USACE convention: hour 24 = midnight of the next day
                            time_str = self._date + " 00:00"
                            when = parse_datetime(time_str)
                            if when:
                                when += timedelta(days=1)
                        else:
                            time_str = self._date + " " + parts[0] + ":00"
                            when = parse_datetime(time_str)
                        if when:
                            # Values are in thousands
                            self.dump_to_db(
                                self._project,
                                DataType.flow,
                                when,
                                val * 1000,
                            )
                        else:
                            logger.error("Cannot parse date: %s", time_str)
            return True

        return True
