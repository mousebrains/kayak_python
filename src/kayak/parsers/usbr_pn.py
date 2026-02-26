"""USBR Pacific Northwest pipe-delimited parser (replaces Parse_USBR_PN.C).

Format: Pipe-delimited with station and type headers.
States: 0=Wait for blank, 1=Collect stations, 2=Parse types, 3+=Data
"""

from __future__ import annotations

import logging
import math

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import celsius_to_fahrenheit, parse_datetime, safe_float

logger = logging.getLogger(__name__)

# USBR PN type codes
_TYPE_MAP = {
    "GH": DataType.gauge,
    "CH": DataType.gauge,
    "Q": DataType.flow,
    "QC": DataType.flow,
    "QD": DataType.flow,
    "WF": DataType.temperature,
}

_CELSIUS_TYPES = set()  # USBR_PN WF is already Fahrenheit


@register("usbr.pn")
class USBRPNParser(BaseParser):
    name = "usbr.pn"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._state = 0
        self._stations: list[str] = []
        self._types: list[DataType | None] = []
        self._is_celsius: list[bool] = []

    def parse_line(self, line: str) -> bool:
        stripped = line.strip()

        if self._state == 0:
            # Wait for station row header
            if not stripped:
                self._state = 1
            return True

        if self._state == 1:
            # Collect station names from pipe-delimited row
            if not stripped:
                if self._stations:
                    self._state = 2
                return True
            parts = [p.strip() for p in stripped.split("|")]
            self._stations = [p.replace(" ", "_") for p in parts if p.strip()]
            return True

        if self._state == 2:
            # Parse type codes
            if not stripped:
                self._state = 3
                return True
            parts = [p.strip() for p in stripped.split("|")]
            self._types = []
            self._is_celsius = []
            for p in parts:
                code = p.strip().upper()
                dtype = _TYPE_MAP.get(code)
                self._types.append(dtype)
                self._is_celsius.append(code in _CELSIUS_TYPES)
            self._state = 3
            return True

        if self._state >= 3:
            return self._parse_data_row(stripped)

        return True

    def _parse_data_row(self, line: str) -> bool:
        if not line:
            return True

        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            return True

        # First part is date/time
        when = parse_datetime(parts[0])
        if when is None:
            return True

        for i, val_str in enumerate(parts[1:]):
            if i >= len(self._stations) or i >= len(self._types):
                break
            if self._types[i] is None:
                continue

            val = safe_float(val_str)
            if val is None or not math.isfinite(val):
                continue

            if self._is_celsius[i]:
                val = celsius_to_fahrenheit(val)

            self.dump_to_db(self._stations[i], self._types[i], when, val)

        return True
