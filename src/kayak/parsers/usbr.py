"""USBR Hydromet CSV parser.

Format: CSV with header row. Columns are ``station_code`` (e.g. ``mado_gh``).
URL parameter ``format=csv`` produces clean CSV without HTML wrapping.

Data codes: Q=FLOW, GH=GAGE, WC/WF=TEMPERATURE, etc.
"""

import math
from dataclasses import dataclass
from typing import Any

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser, ObservationRecord
from kayak.parsers.registry import register
from kayak.utils.conversions import celsius_to_fahrenheit, parse_datetime, safe_float

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

    def parse_records(self, text: str) -> list[ObservationRecord]:
        """Pure: CSV → records. No session, no DB.

        Strips an optional HTML wrapper (USBR sometimes serves CSV
        wrapped in ``<pre>``), parses the header row, then walks data
        rows.  Naive timestamps are localized via ``source_tz_map``
        before being emitted — the resulting record always carries a
        tz-aware UTC datetime when a station mapping exists (and the
        original naive datetime when one doesn't, matching
        ``dump_to_db``'s existing behaviour).
        """
        if "<" in text:
            text = self._strip_html(text)

        # Reset stateful header parsing — parse_records is callable many
        # times on the same instance (tests, retries) and must not carry
        # column metadata from a previous body.
        self._columns = []
        self._header_parsed = False

        records: list[ObservationRecord] = []
        for raw_line in text.splitlines():
            stripped = raw_line.replace("\r", "").strip()
            if not stripped:
                continue
            if not self._header_parsed:
                self._parse_header(stripped)
                continue
            self._collect_data_row(stripped, records)
        return records

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

    def _collect_data_row(self, line: str, records: list[ObservationRecord]) -> None:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            return

        # USBR pn-hydromet publishes each station in its own local timezone
        # (Oregon Pacific, Idaho Mountain, Malheur-County OR Mountain). We
        # parse naive then apply per-station source_tz_map ourselves so the
        # emitted record carries tz-aware UTC — dump_to_db sees an already
        # localized datetime and passes it through.
        when = parse_datetime(parts[0], assume_naive=True)
        if when is None:
            return

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

            records.append(
                ObservationRecord(
                    info.station, info.data_type, self._localize(when, info.station), val
                )
            )
