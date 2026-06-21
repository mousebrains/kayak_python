"""LI-COR public-dashboard timeseries parser.

Parses the JSON returned by the LI-COR cloud timeseries API
(``POST https://www.licor.cloud/api/v2/timeseriesdata``) into observations.
The shared ``levels fetch`` GET path can't POST a request body, so the matching
``levels fetch-licor`` step performs the POST and feeds the response text here;
this class stays a pure ``text -> records`` parser like every other.

Channels are matched by **UUID** — carried in the configured ``fetch_url`` query
params (``?flow=<uuid>&gauge=<uuid>&temperature=<uuid>``) — never by the display
``metricName`` / ``metricUnits`` strings (LI-COR returns e.g. ``"°F"``, and the
display names are not stable identifiers). Timestamps are Unix epoch
milliseconds (absolute UTC), emitted as tz-aware UTC datetimes so the base
``_localize`` step passes them through unchanged; the dataset source's
``timezone`` should be NULL.
"""

import json
import logging
import math
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser, ObservationRecord
from kayak.parsers.registry import register

logger = logging.getLogger(__name__)

# ``fetch_url`` query-param name → kayak DataType. The configured URL carries one
# LI-COR channel UUID per param (e.g. ``?flow=<uuid>&gauge=<uuid>&temperature=<uuid>``).
CHANNEL_PARAMS: dict[str, DataType] = {
    "flow": DataType.flow,
    "gauge": DataType.gauge,  # LI-COR "water level"
    "temperature": DataType.temperature,  # water temperature (not air)
}

# kayak DataType → LI-COR metric name. These are LI-COR *platform* constants
# (like USGS parameter codes), stable across dashboards, so safe to hard-code —
# the per-dashboard identifiers (channel + dashboard UUIDs) stay in dataset
# config. Used by the ``fetch-licor`` step to build the POST request body.
METRIC_NAMES: dict[DataType, str] = {
    DataType.flow: "com.onset.sensordata.waterflow_us",
    DataType.gauge: "com.onset.sensordata.waterlevel_us",
    DataType.temperature: "com.onset.sensordata.watertemperature_us",
}


def channel_map(url: str) -> dict[str, DataType]:
    """Build ``{channelUUID: DataType}`` from a configured fetch_url's query.

    Reads the ``flow``/``gauge``/``temperature`` params (each a LI-COR channel
    UUID). An absent param simply isn't mapped — its response records are
    skipped. A repeated param uses the first value.
    """
    params = parse_qs(urlparse(url).query)
    mapping: dict[str, DataType] = {}
    ambiguous: set[str] = set()
    for param, dtype in CHANNEL_PARAMS.items():
        values = params.get(param)
        if not values:
            continue
        uuid = values[0]
        # A UUID claimed by >1 param is an ambiguous config: refuse to map it
        # (drop its records rather than silently mis-type them — water level into
        # the flow series). `build_request` rejects such a config before any fetch,
        # so this only guards a parser called on a hand-crafted URL.
        if uuid in mapping or uuid in ambiguous:
            ambiguous.add(uuid)
            mapping.pop(uuid, None)
            continue
        mapping[uuid] = dtype
    return mapping


def _valid_points(valid: object, now: datetime) -> list[tuple[datetime, float]]:
    """Parse a record's ``datum.valid`` ``[epoch_ms, value]`` pairs.

    Drops malformed pairs, non-numeric / non-finite values, and points after
    ``now`` (the store layer also rejects future timestamps). Epoch ms → tz-aware
    UTC, so the base ``_localize`` passes them through unchanged.
    """
    points: list[tuple[datetime, float]] = []
    if not isinstance(valid, list):
        return points
    for pair in valid:
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
            continue
        ts_ms, raw = pair
        # Reject JSON booleans explicitly: float(True) is 1.0 and passes the
        # finite check, which would store a bogus 0.0/1.0 reading.
        if isinstance(raw, bool):
            continue
        try:
            when = datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=UTC)
            val = float(raw)
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        if not math.isfinite(val) or when > now:
            continue
        points.append((when, val))
    return points


@register("licor")
class LicorParser(BaseParser):
    """LI-COR public-dashboard timeseries JSON parser.

    Pure ``parse_records`` (text → records); ``parse`` is the thin nwps-style
    wrapper that adds a JSON-parse-error log line and routes records through the
    base ``dump_to_db`` / ``_flush_buffer`` path (cache updates, single-source
    station attribution).
    """

    name = "licor"
    # POST transport: skipped by ``levels fetch`` (GET-only), driven by
    # ``levels fetch-licor``. See BaseParser.transport.
    transport = "POST"

    def parse_records(
        self,
        text: str,
        *,
        now: datetime | None = None,
    ) -> list[ObservationRecord]:
        """Pure: text → records. No session, no DB, no logging side-effects.

        ``now`` defaults to ``datetime.now(UTC)``; tests pin it so the
        future-timestamp guard is deterministic.
        """
        if now is None:
            now = datetime.now(UTC)

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return []

        uuid_to_type = channel_map(self.url)
        query = parse_qs(urlparse(self.url).query)
        # One stable station name per dashboard. The LI-COR source is
        # single-source, so dump_to_db's lone-source fallback attributes every
        # record to it regardless of this string (base.py); keying on the
        # dashboard UUID keeps distinct dashboards distinct if a feed ever
        # becomes multi-source.
        station = (query.get("dashboardUUID") or ["licor"])[0]

        value = data.get("value") if isinstance(data, dict) else None
        records_in = value.get("records") if isinstance(value, dict) else None
        if not isinstance(records_in, list):
            return []

        records: list[ObservationRecord] = []
        for rec in records_in:
            if not isinstance(rec, dict):
                continue
            channel_uuid = rec.get("channelUUID")
            dtype = uuid_to_type.get(channel_uuid) if isinstance(channel_uuid, str) else None
            if dtype is None:
                continue  # unrequested / air-temp / unknown channel — skip cleanly
            datum = rec.get("datum")
            valid = datum.get("valid") if isinstance(datum, dict) else None
            for when, val in _valid_points(valid, now):
                records.append(ObservationRecord(station, dtype, when, val))
        return records

    def parse(self, text: str) -> int:
        """Override to keep a JSON-parse-error log line (nwps pattern).

        ``parse_records`` returns ``[]`` silently on malformed input; this
        wrapper re-runs ``json.loads`` once to emit the ERROR before delegating
        to ``super().parse()`` (the ``super()`` call is required so the buffer
        flushes).
        """
        try:
            json.loads(text)
        except (json.JSONDecodeError, TypeError):
            logger.error("JSON parse error for %s", self.url)
            return 0
        return super().parse(text)
