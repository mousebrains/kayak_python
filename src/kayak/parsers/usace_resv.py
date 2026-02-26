"""USACE Reservoir parser (replaces Parse_USACE_Resv.C).

Pipe-delimited with state machine for reservoir data.
States: 0=Header, 1=Parse section type, 10=INFLOW/OUTFLOW, 20=DISCHARGE
"""

from __future__ import annotations

import logging
import math
import re
from datetime import timedelta

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import parse_datetime, safe_float

logger = logging.getLogger(__name__)


def _mk_name(name: str) -> str:
    """Collapse whitespace into underscores (mirrors mkName helper)."""
    return re.sub(r"[\s]+", "_", name.strip())


@register("usace.resv")
class USACEResvParser(BaseParser):
    name = "usace.resv"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._state = 0
        self._time = None
        self._stacked = False
        self._project = ""
        self._flow = float("inf")
        self._gage = float("inf")

    def parse_line(self, line: str) -> bool:
        tokens = [t.strip() for t in re.split(r"[|+]", line)]
        tokens = [t for t in tokens if t]

        if not tokens:
            return True

        if self._state == 0:
            if "U.S. ARMY ENGINEER DISTRICT" in line.upper() or "U.S. ARMY ENGINEER" in line.upper():
                # Date is in the last token
                if len(tokens) >= 2:
                    dt = parse_datetime(tokens[-1] + " 00:00:00")
                    if dt:
                        self._time = dt - timedelta(hours=12)
                        self._state = 1
                    else:
                        logger.error("Cannot parse date: %s", tokens[-1])
                        return False
            return True

        if self._state == 1:
            if len(tokens) >= 2:
                if tokens[-1].upper() == "OUTFLOW" and tokens[-2].upper() == "INFLOW":
                    self._state = 10
                elif tokens[-1].upper() == "DISCHARGE":
                    self._state = 20
            return True

        if self._state == 10:
            if tokens and tokens[-1].upper() == "CFS":
                self._state = 11
            return True

        if self._state == 11:
            if len(tokens) < 3 or "TOTALS" in tokens[0].upper():
                self._state = 1
                return True

            project = _mk_name(tokens[0])
            inflow = safe_float(tokens[-2])
            outflow = safe_float(tokens[-1])

            if inflow is not None and math.isfinite(inflow):
                self.dump_to_db(project, DataType.inflow, self._time, inflow)
            if outflow is not None and math.isfinite(outflow):
                self.dump_to_db(project, DataType.flow, self._time, outflow)
            return True

        if self._state == 20:
            self._stacked = False
            if tokens and tokens[-1].upper() == "CFS":
                self._state = 21
            return True

        if self._state == 21:
            if len(tokens) < 3:
                self._state = 1
                if self._stacked:
                    self._flush_stacked()
                return True

            if self._stacked:
                if "---" in tokens[0]:
                    project = _mk_name(self._project)
                else:
                    project = _mk_name(self._project + " " + tokens[0])
                self._stacked = False
                if math.isfinite(self._flow):
                    self.dump_to_db(project, DataType.flow, self._time, self._flow)
                if math.isfinite(self._gage):
                    self.dump_to_db(project, DataType.gauge, self._time, self._gage)

            flow = safe_float(tokens[-1])
            gage = safe_float(tokens[-3]) if len(tokens) >= 3 else None

            if (flow is not None and math.isfinite(flow)) or (
                gage is not None and math.isfinite(gage)
            ):
                self._stacked = True
                self._project = tokens[0].strip()
                self._flow = flow if flow is not None else float("inf")
                self._gage = gage if gage is not None else float("inf")

            return True

        return True

    def _flush_stacked(self):
        project = _mk_name(self._project)
        if math.isfinite(self._flow):
            self.dump_to_db(project, DataType.flow, self._time, self._flow)
        if math.isfinite(self._gage):
            self.dump_to_db(project, DataType.gauge, self._time, self._gage)
        self._stacked = False
