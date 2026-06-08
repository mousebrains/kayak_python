"""NWPS API parser for NOAA National Water Prediction Service.

Endpoint: https://api.water.noaa.gov/nwps/v1/gauges/{LID}/stageflow/observed

Returns JSON with stage (ft) and flow (kcfs) observations.
Only observed data is stored — forecasts are not used.
"""

import json
import logging
import re
from datetime import UTC, datetime

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser, ObservationRecord
from kayak.parsers.registry import register
from kayak.utils.conversions import kcfs_to_cfs, parse_datetime

logger = logging.getLogger(__name__)

# Sentinel values used by NWPS API for unavailable data
_MISSING_VALUES = {-999, -9999}


@register("nwps")
class NWPSParser(BaseParser):
    """NOAA National Water Prediction Service API parser.

    Parses JSON from the NWPS stageflow/observed endpoint. Extracts stage
    (ft) and flow (cfs or kcfs, converted to cfs). Sentinel values -999
    and -9999 are treated as missing data.

    T3.1 status: ``parse_records`` is the pure entry point;
    ``parse`` is a thin wrapper that feeds the records through the
    legacy ``dump_to_db``/``_flush_buffer`` path so the rest of the
    pipeline (latest-observation cache, gauge cache, unknown-station
    drop+record path) keeps working unchanged.
    """

    name = "nwps"

    def parse_records(
        self,
        text: str,
        *,
        now: datetime | None = None,
    ) -> list[ObservationRecord]:
        """Pure: text → records. No session, no DB, no logging side-effects.

        ``now`` defaults to ``datetime.now(UTC)``; tests pin it to
        defeat the clock-drift races that bit the property-test sweep
        (commit ``33e4998``).
        """
        if now is None:
            now = datetime.now(UTC)

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return []

        # Extract station LID from URL path: .../gauges/{LID}/stageflow/...
        station = self._extract_station(self.url)

        primary_units = (data.get("primaryUnits") or "").lower()
        secondary_units = (data.get("secondaryUnits") or "").lower()

        has_stage = primary_units == "ft"
        has_flow = secondary_units in ("kcfs", "cfs")
        flow_is_kcfs = secondary_units == "kcfs"

        records: list[ObservationRecord] = []
        for entry in data.get("data") or []:
            valid_time = entry.get("validTime")
            if not valid_time:
                continue

            # Strip trailing "Z" — parse_datetime handles UTC by default
            when = parse_datetime(valid_time.rstrip("Z"))
            if when is None or when > now:
                continue

            stage, flow = self._extract_stage_and_flow(entry, has_stage, has_flow, flow_is_kcfs)
            if stage is not None:
                records.append(ObservationRecord(station, DataType.gauge, when, stage))
            if flow is not None:
                records.append(ObservationRecord(station, DataType.flow, when, flow))

        return records

    def parse(self, text: str) -> int:
        """Override to keep the prior JSON-parse-error log line.

        The pure ``parse_records`` returns ``[]`` silently on malformed
        input so test callers don't have to suppress logs; this wrapper
        re-runs ``json.loads`` once (cheap) to emit the ERROR before
        delegating to ``super().parse()``.
        """
        try:
            json.loads(text)
        except (json.JSONDecodeError, TypeError):
            logger.error("JSON parse error for %s", self.url)
            return 0
        return super().parse(text)

    @staticmethod
    def _extract_stage_and_flow(
        entry: dict,
        has_stage: bool,
        has_flow: bool,
        flow_is_kcfs: bool,
    ) -> tuple[float | None, float | None]:
        """Extract (stage_ft, flow_cfs) from one NWPS entry.

        Returns None for either component when the unit dispatch says it's
        not present, the value is missing, or the value fails a unit
        sanity check (negative flow). Conversion from kcfs to cfs happens
        here so the caller only sees cfs.
        """
        stage: float | None = None
        if has_stage:
            primary = entry.get("primary")
            if primary is not None and primary not in _MISSING_VALUES:
                stage = float(primary)

        flow: float | None = None
        if has_flow:
            secondary = entry.get("secondary")
            if secondary is not None and secondary not in _MISSING_VALUES:
                cfs = kcfs_to_cfs(float(secondary)) if flow_is_kcfs else float(secondary)
                if cfs >= 0:
                    flow = cfs

        return stage, flow

    @staticmethod
    def _extract_station(url: str) -> str:
        """Extract station LID from NWPS URL path."""
        m = re.search(r"/gauges/([^/]+)", url)
        return m.group(1) if m else ""
