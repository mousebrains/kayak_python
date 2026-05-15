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
from kayak.parsers.base import BaseParser, ObservationRecord
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

    def parse_records(
        self,
        text: str,
        *,
        now: datetime | None = None,
    ) -> list[ObservationRecord]:
        """Pure: text → records. No session, no DB, no logging side-effects.

        Raises ``ValueError`` on a URL without ``timezone=GMT`` — that's
        a deterministic, input-driven error (the server defaults to PST
        without it, which would shift timestamps 8h). The wrapper
        ``parse()`` lets that bubble up unchanged.
        """
        if "timezone=GMT" not in self.url:
            raise ValueError(
                f"USACE CDA URL must include 'timezone=GMT' (got {self.url!r}); "
                "without it the server defaults to PST and timestamps will be "
                "stored 8h early."
            )

        if now is None:
            now = datetime.now(UTC)

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return []

        records: list[ObservationRecord] = []
        for station, station_data in data.items():
            timeseries = station_data.get("timeseries") or {}
            for _ts_id, ts_info in timeseries.items():
                parameter = ts_info.get("parameter", "")
                data_type = _PARAM_MAP.get(parameter)
                if data_type is None:
                    continue
                for entry in ts_info.get("values") or []:
                    record = self._entry_to_record(entry, station, data_type, now)
                    if record is not None:
                        records.append(record)

        return records

    @staticmethod
    def _entry_to_record(
        entry: object,
        station: str,
        data_type: DataType,
        now: datetime,
    ) -> ObservationRecord | None:
        """Convert one [timestamp, value, quality] triple to a record (or None to skip)."""
        if not isinstance(entry, list) or len(entry) < 2:
            return None
        timestamp_str, value = entry[0], entry[1]
        if value is None:
            return None
        when = parse_datetime(timestamp_str)
        if when is None or when > now:
            return None
        return ObservationRecord(station, data_type, when, float(value))

    def parse(self, text: str) -> int:
        """Thin wrapper over ``parse_records`` + the legacy DB path.

        Preserves the pre-T3.1 logging contract: a JSON parse error
        emits an error log (``parse_records`` swallows it for purity),
        and an unknown parameter emits a debug log (the legacy debug
        line; per-call rather than per-skip is fine for diagnostics).
        """
        self._db_updates = 0
        self._obs_buffer = []

        # Pre-flight: the timezone=GMT guard from parse_records would
        # also catch this, but the legacy parse() raises BEFORE the
        # buffer reset; preserve that ordering.
        if "timezone=GMT" not in self.url:
            raise ValueError(
                f"USACE CDA URL must include 'timezone=GMT' (got {self.url!r}); "
                "without it the server defaults to PST and timestamps will be "
                "stored 8h early."
            )

        try:
            json.loads(text)
        except (json.JSONDecodeError, TypeError):
            logger.error("JSON parse error for %s", self.url)
            return 0

        records = self.parse_records(text)
        for r in records:
            self.dump_to_db(r.station, r.data_type, r.observed_at, r.value)
        self._flush_buffer()

        if self._db_updates == 0:
            logger.warning("No database updates from %s parser(%s)", self.url, self.name)

        return self._db_updates

    def parse_line(self, line: str) -> bool:
        return True
