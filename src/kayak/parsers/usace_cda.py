"""USACE Corps Data Access parser.

Endpoint:
  https://www.nwd-wc.usace.army.mil/dd/common/web_service/webexec/getjson
  ?query=["GPR.Flow-Out.Inst.0.0.Best"]&timezone=GMT&backward=2d&forward=0d

Returns JSON keyed by station with nested timeseries containing
[timestamp, value, quality_flag] triples.  Values are already in CFS.
"""

from __future__ import annotations

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
    name = "usace.cda"

    def parse(self, text: str) -> int:
        """Parse JSON response from USACE web service."""
        self._db_updates = 0

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            logger.error("JSON parse error for %s", self.url)
            return 0

        now = datetime.now(UTC)

        for station, station_data in data.items():
            timeseries = station_data.get("timeseries") or {}
            for _ts_id, ts_info in timeseries.items():
                parameter = ts_info.get("parameter", "")
                data_type = _PARAM_MAP.get(parameter)
                if data_type is None:
                    logger.debug("Skipping unknown parameter %s", parameter)
                    continue

                for entry in ts_info.get("values") or []:
                    if not isinstance(entry, list) or len(entry) < 2:
                        continue

                    timestamp_str, value = entry[0], entry[1]
                    if value is None:
                        continue

                    when = parse_datetime(timestamp_str)
                    if when is None or when > now:
                        continue

                    self.dump_to_db(station, data_type, when, float(value))

        if self._db_updates == 0:
            logger.warning(
                "No database updates from %s parser(%s)", self.url, self.name
            )

        return self._db_updates

    def parse_line(self, line: str) -> bool:
        return True
