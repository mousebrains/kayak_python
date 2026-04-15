"""Washington State DOE parser.

Format: Space/tab delimited with "DATE TIME" header.
States: 0=Wait for header, 1=Skip separator, 2+=Data rows

Type detection from header: Water=TEMPERATURE, Stage=GAGE, else FLOW
Quality field (last column) must be 0-200 for valid data.
"""

import logging
import math
from typing import Any

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import celsius_to_fahrenheit, parse_datetime, safe_float

logger = logging.getLogger(__name__)


@register("wa.gov")
class WaGovParser(BaseParser):
    """Washington State Dept. of Ecology real-time data parser.

    Parses tab-delimited text from WA DOE real-time monitoring stations.
    Uses a three-state machine: state 0 finds the station header, state 1
    reads column headers, state 2 reads data rows. Filters out rows with
    quality codes >= 200 (suspect or rejected data).
    """

    name = "wa.gov"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._state = 0
        self._station = ""
        self._data_type = DataType.flow

    def parse_line(self, line: str) -> bool:
        if not line.strip():
            return True

        parts = line.split()
        if len(parts) < 2:
            return True

        if self._state == 0:
            if parts[0] == "DATE" and parts[1] == "TIME":
                self._state = 1
                self._data_type = DataType.flow
                if len(parts) >= 3:
                    type_hint = parts[2].lower()
                    if type_hint.startswith("water"):
                        self._data_type = DataType.temperature
                    elif type_hint.startswith("stage"):
                        self._data_type = DataType.gauge
            else:
                # Look for station name (format: STATIONID--description)
                if "--" in parts[0]:
                    self._station = parts[0].split("--")[0]
            return True

        if self._state == 1:
            if parts[0].startswith("---"):
                self._state = 2
            return True

        # State 2+: Data rows
        if parts[0] == "Quality":
            self._state = 0
            return True

        if not self._station or len(parts) <= 3:
            return True

        if "No Data" in line:
            return True

        # Last column is quality code; 0 means "no quality code available" in
        # WA DOE data, treated as suspect.  Valid quality codes are 1-199.
        quality = safe_float(parts[-1])
        if quality is None or quality <= 0 or quality >= 200:
            return True

        time_str = parts[0] + " " + parts[1]
        when = parse_datetime(time_str)
        if when is None:
            return True

        val = safe_float(parts[2])
        if val is None or not math.isfinite(val):
            return True

        if self._data_type == DataType.temperature:
            val = celsius_to_fahrenheit(val)

        self.dump_to_db(self._station, self._data_type, when, val)
        return True
