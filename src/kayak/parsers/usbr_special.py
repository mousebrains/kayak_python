"""USBR Special format parser (replaces Parse_USBR_Special.C).

Format: Space-delimited with timezone codes.
Zone mapping: P=UTC, M=MST, C=CST, E=EST
Data format: ".A station DH/field value"
"""

from __future__ import annotations

import logging
import math

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import parse_datetime, safe_float

logger = logging.getLogger(__name__)

# USBR Special type codes
_TYPE_MAP = {
    "QRIRG": DataType.FLOW,
    "HGIRG": DataType.GAGE,
}

# Timezone zone codes
_ZONE_MAP = {
    "P": "UTC",
    "M": "MST",
    "C": "CST",
    "E": "EST",
}


@register("usbr.special")
class USBRSpecialParser(BaseParser):
    name = "usbr.special"

    def parse_line(self, line: str) -> bool:
        if not line.strip() or not line.startswith(".A"):
            return True

        parts = line.split()
        if len(parts) < 4:
            return True

        # .A STATION DH{datetime}/{zone}{type} VALUE
        station = parts[1].replace(" ", "_")

        # Parse DH field: DH2024061512/PQRIRG
        dh_field = parts[2]
        if not dh_field.startswith("DH"):
            return True

        dh_parts = dh_field[2:].split("/")
        if len(dh_parts) != 2:
            return True

        datetime_str = dh_parts[0]
        type_info = dh_parts[1]

        # First char of type_info is timezone code
        tz_code = type_info[0] if type_info else "P"
        data_code = type_info[1:] if len(type_info) > 1 else ""

        tz_name = _ZONE_MAP.get(tz_code, "UTC")
        data_type = _TYPE_MAP.get(data_code)
        if data_type is None:
            return True

        # Parse datetime (format: YYYYMMDDHHmm or YYYYMMDDHH)
        when = parse_datetime(datetime_str, tz_name)
        if when is None:
            return True

        val = safe_float(parts[3])
        if val is None or not math.isfinite(val):
            return True

        self.dump_to_db(station, data_type, when, val)
        return True
