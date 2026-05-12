"""USACE Corps Data Access parser.

Endpoint:
  https://www.nwd-wc.usace.army.mil/dd/common/web_service/webexec/getjson
  ?query=["GPR.Flow-Out.Inst.0.0.Best"]&timezone=GMT&backward=2d&forward=0d

Returns JSON keyed by station with nested timeseries containing
[timestamp, value, quality_flag] triples.  Values are already in CFS.
"""

import json
import logging
from datetime import UTC, datetime

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import parse_datetime

logger = logging.getLogger(__name__)

# Map CWMS parameter names to DataType
_PARAM_MAP: dict[str, DataType] = {
    "Flow-Out": DataType.flow,
    "Flow-In": DataType.inflow,
    "Flow-Spill": DataType.flow,
    "Elev-Forebay": DataType.gauge,
    "Elev-Tailwater": DataType.gauge,
}


@register("usace.cda")
class USACECDAParser(BaseParser):
    """US Army Corps of Engineers CDA JSON parser.

    Parses time-series JSON from the USACE Columbia Data Access API.
    Handles multi-parameter responses (flow, gage, temperature) with
    unit conversion from kcfs to cfs.
    """

    name = "usace.cda"

    def parse(self, text: str) -> int:
        """Parse JSON response from USACE web service."""
        # USACE CDA returns naive timestamps; server TZ is server-default (PST)
        # unless the URL pins it. parse_datetime() stamps UTC on naive inputs,
        # so timestamps are only correct if the URL requests UTC explicitly.
        if "timezone=GMT" not in self.url:
            raise ValueError(
                f"USACE CDA URL must include 'timezone=GMT' (got {self.url!r}); "
                "without it the server defaults to PST and timestamps will be "
                "stored 8h early."
            )

        self._db_updates = 0
        self._obs_buffer = []

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            logger.error("JSON parse error for %s", self.url)
            return 0

        now = datetime.now(UTC)

        for station, station_data in data.items():
            self._parse_station(station, station_data, now)

        self._flush_buffer()

        if self._db_updates == 0:
            logger.warning("No database updates from %s parser(%s)", self.url, self.name)

        return self._db_updates

    def _parse_station(self, station: str, station_data: dict, now: datetime) -> None:
        """Walk one station's timeseries, dispatching each known parameter."""
        timeseries = station_data.get("timeseries") or {}
        for _ts_id, ts_info in timeseries.items():
            parameter = ts_info.get("parameter", "")
            data_type = _PARAM_MAP.get(parameter)
            if data_type is None:
                logger.debug("Skipping unknown parameter %s", parameter)
                continue
            for entry in ts_info.get("values") or []:
                self._emit_entry(entry, station, data_type, now)

    def _emit_entry(self, entry: object, station: str, data_type: DataType, now: datetime) -> None:
        """Emit one observation from a [timestamp, value, quality] triple."""
        if not isinstance(entry, list) or len(entry) < 2:
            return
        timestamp_str, value = entry[0], entry[1]
        if value is None:
            return
        when = parse_datetime(timestamp_str)
        if when is None or when > now:
            return
        self.dump_to_db(station, data_type, when, float(value))

    def parse_line(self, line: str) -> bool:
        return True
