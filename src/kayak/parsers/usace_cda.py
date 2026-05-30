"""USACE Corps Data Access parser.

Endpoint:
  https://www.nwd-wc.usace.army.mil/dd/common/web_service/webexec/getjson
  ?query=["GPR.Flow-Out.Inst.0.0.Best"]&timezone=GMT&backward=2d&forward=0d

Returns JSON keyed by station with nested timeseries containing
[timestamp, value, quality_flag] triples. Each timeseries carries a
``units`` field: flow is ``cfs`` for the Willamette dams' ``.Inst.0.0.Best``
series but ``kcfs`` for the lower-Columbia dams' ``.Ave.1Hour.1Hour`` series,
so kcfs is scaled to cfs per-series. Elevation is ``ft`` (stored as-is).
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
    Handles multi-parameter responses (flow, gage), scaling kcfs flow
    series to cfs from each timeseries' ``units`` field.
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
                units = (ts_info.get("units") or "").strip().lower()
                for entry in ts_info.get("values") or []:
                    record = self._entry_to_record(entry, station, data_type, units, now)
                    if record is not None:
                        records.append(record)

        return records

    @staticmethod
    def _entry_to_record(
        entry: object,
        station: str,
        data_type: DataType,
        units: str,
        now: datetime,
    ) -> ObservationRecord | None:
        """Convert one [timestamp, value, quality] triple to a record (or None to skip).

        ``units`` is the timeseries' lowercased unit string. The lower-Columbia
        dams report flow in ``kcfs``; scale those to the project's canonical
        ``cfs``. Everything else (cfs, ft, or a missing unit) is stored as-is.
        """
        if not isinstance(entry, list) or len(entry) < 2:
            return None
        timestamp_str, value = entry[0], entry[1]
        if value is None:
            return None
        when = parse_datetime(timestamp_str)
        if when is None or when > now:
            return None
        result = float(value)
        if units == "kcfs":
            result *= 1000.0
        return ObservationRecord(station, data_type, when, result)

    def parse(self, text: str) -> int:
        """Override to keep the prior JSON-parse-error log line.

        ``parse_records`` already enforces the ``timezone=GMT`` URL guard
        (raises ``ValueError``) and returns ``[]`` silently on malformed
        JSON; this wrapper re-runs ``json.loads`` once to emit the ERROR
        before delegating to ``super().parse()``.
        """
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
        return super().parse(text)
