"""Washington State DOE parser.

Format: Space/tab delimited with "DATE TIME" header.
States: 0=Wait for header, 1=Skip separator, 2+=Data rows

Type detection from header: Water=TEMPERATURE, Stage=GAGE, else FLOW
Quality field (last column) must be 0-200 for valid data.
"""

import math

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser, ObservationRecord
from kayak.parsers.registry import register
from kayak.utils.conversions import celsius_to_fahrenheit, parse_datetime, safe_float


@register("wa.gov")
class WaGovParser(BaseParser):
    """Washington State Dept. of Ecology real-time data parser.

    Parses tab-delimited text from WA DOE real-time monitoring stations.
    Uses a three-state machine: state 0 finds the station header, state 1
    reads column headers, state 2 reads data rows. Filters out rows with
    quality codes >= 200 (suspect or rejected data).
    """

    name = "wa.gov"

    def parse_records(self, text: str) -> list[ObservationRecord]:
        """Pure: WA DOE text → records. No session, no DB.

        Drives the same three-state machine the legacy ``parse_line``
        used, but reset per call: state 0 finds the ``STATIONID--name``
        line, state 1 waits for the ``---`` separator, state 2+ reads
        data rows.  A line beginning with ``Quality`` flips back to
        state 0 so multi-station bodies parse end-to-end.

        Naive timestamps are localized via ``source_tz_map`` before
        being emitted (sources.yaml seeds wa.gov stations with
        ``timezone: Etc/GMT+8`` — PST year-round, no DST).
        """
        state = 0
        station = ""
        data_type = DataType.flow
        records: list[ObservationRecord] = []

        for raw_line in text.splitlines():
            line = raw_line.replace("\r", "")
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) < 2:
                continue

            if state == 0:
                state, station, data_type = self._handle_header(parts, station, data_type)
                continue
            if state == 1:
                if parts[0].startswith("---"):
                    state = 2
                continue
            # State 2+: Data rows
            if parts[0] == "Quality":
                state = 0
                continue
            record = self._record_from_row(line, parts, station, data_type)
            if record is not None:
                records.append(record)
        return records

    @staticmethod
    def _handle_header(
        parts: list[str], station: str, data_type: DataType
    ) -> tuple[int, str, DataType]:
        """Pick up either a station header or the ``DATE TIME …`` row.

        Returns ``(next_state, station, data_type)``.
        """
        if parts[0] == "DATE" and parts[1] == "TIME":
            new_dt = DataType.flow
            if len(parts) >= 3:
                type_hint = parts[2].lower()
                if type_hint.startswith("water"):
                    new_dt = DataType.temperature
                elif type_hint.startswith("stage"):
                    new_dt = DataType.gauge
            return 1, station, new_dt
        if "--" in parts[0]:
            # Station header looks like "STATIONID--description"
            return 0, parts[0].split("--")[0], data_type
        return 0, station, data_type

    def _record_from_row(
        self,
        line: str,
        parts: list[str],
        station: str,
        data_type: DataType,
    ) -> ObservationRecord | None:
        """State-2 body: build one record per data row when fully valid."""
        if not station or len(parts) <= 3:
            return None
        if "No Data" in line:
            return None

        # Last column is quality code; 0 means "no quality code available" in
        # WA DOE data, treated as suspect. Valid quality codes are 1-199.
        quality = safe_float(parts[-1])
        if quality is None or quality <= 0 or quality >= 200:
            return None

        # Timestamps are naive; source_tz_map (seeded from sources.yaml
        # stations: block, typically "Etc/GMT+8" — PST year-round, no DST)
        # gets applied by self._localize so the record carries tz-aware UTC.
        time_str = parts[0] + " " + parts[1]
        when = parse_datetime(time_str, assume_naive=True)
        if when is None:
            return None

        val = safe_float(parts[2])
        if val is None or not math.isfinite(val):
            return None

        if data_type == DataType.temperature:
            val = celsius_to_fahrenheit(val)

        return ObservationRecord(station, data_type, self._localize(when, station), val)
