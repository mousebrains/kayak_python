"""USBR comma-delimited parser (replaces Parse_USBR.C).

Format: Comma-delimited with BEGIN/END DATA markers.
States: 0=Wait for BEGIN DATA, 1=Parse header, 2=Data rows

Data codes: Q=FLOW, GH=GAGE, WC/WF=TEMPERATURE, etc.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import celsius_to_fahrenheit, parse_datetime, safe_float

logger = logging.getLogger(__name__)

# USBR data type code to DataType mapping
_CODE_MAP: dict[str, DataType | None] = {
    "Q": DataType.flow,
    "QD": DataType.flow,
    "QI": DataType.inflow,
    "QJ": DataType.flow,
    "QR": DataType.flow,
    "QU": DataType.flow,
    "GH": DataType.gauge,
    "HP": DataType.gauge,
    "HT": DataType.gauge,
    "FB": DataType.gauge,
    "WC": DataType.temperature,  # Celsius
    "WF": DataType.temperature,  # Fahrenheit
    "WS": DataType.temperature,
}

# Codes whose values are in Celsius and need conversion
_CELSIUS_CODES = {"WC"}


@dataclass
class _StationInfo:
    station: str
    code: str
    data_type: DataType
    is_celsius: bool = False


@register("usbr")
class USBRParser(BaseParser):
    name = "usbr"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._state = 0
        self._columns: list[_StationInfo] = []

    def parse_line(self, line: str) -> bool:
        stripped = line.strip()

        if self._state == 0:
            if stripped.upper().startswith("BEGIN DATA"):
                self._state = 1
            return True

        if self._state == 1:
            # Header: DATE TIME | STN1,CODE1 | STN2,CODE2 | ...
            parts = [p.strip() for p in stripped.split("|")]
            self._columns = []
            for p in parts[1:]:  # Skip DATE TIME
                if "," not in p:
                    continue
                stn, code = p.split(",", 1)
                stn = stn.strip().replace(" ", "_")
                code = code.strip().upper()
                dtype = _CODE_MAP.get(code)
                if dtype:
                    self._columns.append(_StationInfo(
                        station=stn, code=code, data_type=dtype,
                        is_celsius=(code in _CELSIUS_CODES),
                    ))
                else:
                    self._columns.append(None)
            self._state = 2
            return True

        if self._state == 2:
            if stripped.upper().startswith("END DATA"):
                self._state = 0
                return True
            return self._parse_data_row(stripped)

        return True

    def _parse_data_row(self, line: str) -> bool:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            return True

        # First two parts are date and time
        time_str = parts[0] + " " + parts[1] if len(parts) > 1 else parts[0]
        when = parse_datetime(time_str)
        if when is None:
            return True

        for i, info in enumerate(self._columns):
            if info is None:
                continue
            data_idx = i + 2  # offset past date+time
            if data_idx >= len(parts):
                continue

            val = safe_float(parts[data_idx])
            if val is None or not math.isfinite(val):
                continue

            if info.is_celsius:
                val = celsius_to_fahrenheit(val)

            self.dump_to_db(info.station, info.data_type, when, val)

        return True
