"""NWPS API parser for NOAA National Water Prediction Service.

Endpoint: https://api.water.noaa.gov/nwps/v1/gauges/{LID}/stageflow/observed

Returns JSON with stage (ft) and flow (kcfs) observations.
Only observed data is stored — forecasts are not used.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import kcfs_to_cfs, parse_datetime

logger = logging.getLogger(__name__)

# Sentinel values used by NWPS API for unavailable data
_MISSING_VALUES = {-999, -9999}


@register("nwps")
class NWPSParser(BaseParser):
    name = "nwps"

    def parse(self, text: str) -> int:
        """Parse JSON response from NWPS stageflow/observed endpoint."""
        self._db_updates = 0
        self._obs_buffer = []

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            logger.error("JSON parse error for %s", self.url)
            return 0

        # Extract station LID from URL path: .../gauges/{LID}/stageflow/...
        station = self._extract_station(self.url)

        primary_units = (data.get("primaryUnits") or "").lower()
        secondary_units = (data.get("secondaryUnits") or "").lower()

        has_stage = primary_units == "ft"
        has_flow = secondary_units in ("kcfs", "cfs")
        flow_is_kcfs = secondary_units == "kcfs"

        now = datetime.now(UTC)

        for entry in data.get("data") or []:
            valid_time = entry.get("validTime")
            if not valid_time:
                continue

            # Strip trailing "Z" — parse_datetime handles UTC by default
            when = parse_datetime(valid_time.rstrip("Z"))
            if when is None or when > now:
                continue

            # Stage (primary)
            if has_stage:
                primary = entry.get("primary")
                if primary is not None and primary not in _MISSING_VALUES:
                    self.dump_to_db(station, DataType.gauge, when, float(primary))

            # Flow (secondary)
            if has_flow:
                secondary = entry.get("secondary")
                if secondary is not None and secondary not in _MISSING_VALUES:
                    flow_cfs = kcfs_to_cfs(float(secondary)) if flow_is_kcfs else float(secondary)
                    if flow_cfs >= 0:
                        self.dump_to_db(station, DataType.flow, when, flow_cfs)

        self._flush_buffer()

        if self._db_updates == 0:
            logger.warning("No database updates from %s parser(%s)", self.url, self.name)

        return self._db_updates

    def parse_line(self, line: str) -> bool:
        return True

    @staticmethod
    def _extract_station(url: str) -> str:
        """Extract station LID from NWPS URL path."""
        m = re.search(r"/gauges/([^/]+)", url)
        return m.group(1) if m else ""
