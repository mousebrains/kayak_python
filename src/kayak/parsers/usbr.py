"""USBR Hydromet CSV parser.

Format: CSV with header row. Columns are ``station_code`` (e.g. ``mado_gh``).
URL parameter ``format=csv`` produces clean CSV without HTML wrapping.

Data codes: Q=FLOW, GH=GAGE, WC/WF=TEMPERATURE, etc.
"""

import logging
import math
from dataclasses import dataclass
from typing import Any

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import celsius_to_fahrenheit, parse_datetime, safe_float

logger = logging.getLogger(__name__)

# USBR data type code to DataType mapping
_CODE_MAP: dict[str, DataType | None] = {
    "q": DataType.flow,
    "qd": DataType.flow,
    "qi": DataType.inflow,
    "qj": DataType.flow,
    "qr": DataType.flow,
    "qu": DataType.flow,
    "gh": DataType.gauge,
    "hp": DataType.gauge,
    "ht": DataType.gauge,
    "fb": DataType.gauge,
    "wc": DataType.temperature,  # Celsius
    "wf": DataType.temperature,  # Fahrenheit
    # ws = water-surface temperature. USBR Hydromet ships it in °F; live-DB
    # spot-check across 7 stations (BENO, CSCI, DEBO, HRSI, ROMO, UMAO, WICO)
    # showed min ≈ 37 and typical max 48-52, consistent with Fahrenheit water
    # temperatures in the Pacific Northwest. If USBR ever publishes a new
    # station whose values land in the Celsius range (0-30), add "ws" to
    # _CELSIUS_CODES and document the flip here.
    "ws": DataType.temperature,
}

# Codes whose values are in Celsius and need conversion to Fahrenheit
# before storage. See celsius_to_fahrenheit in kayak.utils.conversions.
_CELSIUS_CODES = {"wc"}


@dataclass
class _ColumnInfo:
    station: str
    code: str
    data_type: DataType
    is_celsius: bool = False


@register("usbr")
class USBRParser(BaseParser):
    """US Bureau of Reclamation Hydromet CSV parser.

    Parses CSV data from USBR Hydromet web service. Handles multi-station
    responses with columns like QD (flow), GH (gage height), and QJ (inflow).
    Temperature values are converted from Celsius to Fahrenheit.
    """

    name = "usbr"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._columns: list[_ColumnInfo | None] = []
        self._header_parsed = False

    def parse(self, text: str) -> int:
        """Strip HTML wrapper (if present) before parsing lines."""
        if "<" in text:
            text = self._strip_html(text)
        return super().parse(text)

    def parse_line(self, line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return True

        # CSV format: first line is header, rest are data
        if not self._header_parsed:
            return self._parse_header(stripped)
        return self._parse_data_row(stripped)

    def _parse_header(self, line: str) -> bool:
        """Parse CSV header: ``DateTime,station_code,station_code,...``"""
        parts = [p.strip() for p in line.split(",")]
        self._columns = []
        for col in parts[1:]:  # Skip DateTime
            # Column format: "station_code" e.g. "mado_gh", "romo_q"
            underscore = col.rfind("_")
            if underscore < 1:
                self._columns.append(None)
                continue
            station = col[:underscore].upper()
            code = col[underscore + 1 :]
            data_type = _CODE_MAP.get(code)
            if data_type:
                self._columns.append(
                    _ColumnInfo(
                        station=station,
                        code=code,
                        data_type=data_type,
                        is_celsius=(code in _CELSIUS_CODES),
                    )
                )
            else:
                self._columns.append(None)
        self._header_parsed = True
        return True

    def _parse_data_row(self, line: str) -> bool:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            return True

        when = parse_datetime(parts[0])
        if when is None:
            return True

        for i, info in enumerate(self._columns):
            if info is None:
                continue
            data_idx = i + 1  # offset past DateTime
            if data_idx >= len(parts):
                continue

            val = safe_float(parts[data_idx])
            if val is None or not math.isfinite(val):
                continue

            if info.is_celsius:
                val = celsius_to_fahrenheit(val)

            self.dump_to_db(info.station, info.data_type, when, val)

        return True
