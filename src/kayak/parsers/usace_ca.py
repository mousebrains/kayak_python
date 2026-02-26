"""USACE California fixed-width parser (replaces Parse_USACE_Ca.C).

Complex multi-line header with dashes marking column boundaries.
Extracts flow, gage, inflow, outflow from fixed-width data.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import parse_datetime, safe_float

logger = logging.getLogger(__name__)


@dataclass
class _ColumnDef:
    name: str
    data_type: DataType
    start: int
    length: int


@register("usace.ca")
class USACECaParser(BaseParser):
    name = "usace.ca"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._state = 0
        self._lines: list[str] = []  # Pre-header lines for name extraction
        self._columns: list[_ColumnDef] = []
        self._name_prefix = ""

        # Extract name prefix from URL query string
        url = self.url
        qmark = url.rfind("?")
        if qmark >= 0:
            self._name_prefix = url[qmark + 1:] + " "

    def parse_line(self, line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            self._state = 0
            self._lines.clear()
            return True

        if self._state == 0:
            parts = stripped.split()
            if parts and parts[0] == "Date":
                self._parse_header(line, parts)
                self._state = 1
                self._lines.clear()
            else:
                self._lines.append(line)
            return True

        # Data rows
        parts = line.split()
        if not parts or len(parts) < 2:
            return True

        # First two tokens: date and time
        time_str = parts[0] + " " + parts[1]
        time_val = parts[1]
        if time_val == "2400":
            time_str = parts[0] + " 2359"

        when = parse_datetime(time_str)
        if not when:
            return True

        for col in self._columns:
            if col.start < len(line):
                end = col.start + col.length if col.length > 0 else len(line)
                field = line[col.start:end].strip()
                if field:
                    val = safe_float(field)
                    if val is not None and math.isfinite(val):
                        self.dump_to_db(col.name, col.data_type, when, val)

        return True

    def _parse_header(self, line: str, tokens: list[str]):
        """Parse the header line to determine column positions and types."""
        # Look for patterns like "Stage Flow", "Outflow", "Inflow"
        for i in range(len(tokens) - 1, 0, -1):
            token = tokens[i]
            token_upper = token.upper()

            if token_upper in ("FLOW", "OUTFLOW"):
                dtype = DataType.FLOW if token_upper == "FLOW" else DataType.FLOW
                pos = line.rfind(token)
                if pos >= 0:
                    name = self._extract_name(pos)
                    self._columns.append(_ColumnDef(
                        name=name, data_type=dtype,
                        start=pos, length=max(len(token), 8),
                    ))

            elif token_upper == "INFLOW":
                pos = line.rfind(token)
                if pos >= 0:
                    name = self._extract_name(pos)
                    self._columns.append(_ColumnDef(
                        name=name, data_type=DataType.INFLOW,
                        start=pos, length=max(len(token), 8),
                    ))

            elif token_upper == "STAGE" and i + 1 < len(tokens) and tokens[i + 1].upper() == "FLOW":
                pos = line.rfind(token)
                if pos >= 0:
                    name = self._extract_name(pos)
                    self._columns.append(_ColumnDef(
                        name=name, data_type=DataType.GAGE,
                        start=pos, length=max(len(token), 6),
                    ))

    def _extract_name(self, col_pos: int) -> str:
        """Extract a station name from the pre-header lines at the given column."""
        ws = " -@.\t\n"
        name_parts = []

        start = max(0, len(self._lines) - 3)
        for i in range(start, len(self._lines)):
            line = self._lines[i]
            if col_pos < len(line):
                # Get a chunk around the column position
                chunk = line[max(0, col_pos - 5):col_pos + 15].strip(ws)
                chunk = re.sub(r"[-]+", "", chunk).strip()
                if chunk and chunk not in ("Computed", "Outflow", "Inflow", "Total"):
                    name_parts.append(chunk)

        name = " ".join(name_parts).strip(ws)
        name = re.sub(r"[*]", "", name)
        # Collapse whitespace to underscores
        name = re.sub(r"[\s\-@.]+", "_", name).strip("_")
        return self._name_prefix + name if name else self._name_prefix.strip()
