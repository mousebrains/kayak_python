"""Abstract base parser.

Each parser processes text fetched from a government agency data source.
Subclasses implement ``parse_line()``, which is called for each line.
"""

import html
import logging
import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from kayak.db.data_db import (
    get_negative_flow_source_ids,
    store_observations,
    update_latest,
    update_latest_gauge,
)
from kayak.db.models import DataType, GaugeSource, Source

logger = logging.getLogger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")


class BaseParser(ABC):
    """Abstract base for all data source parsers.

    Subclasses must implement ``parse_line()`` and ``name``.
    """

    name: str = "base"  # Override in subclass

    def __init__(
        self,
        url: str,
        session: Session,
        *,
        source_id: int | None = None,
        source_map: dict[str, int] | None = None,
        source_tz_map: dict[str, str] | None = None,
        dry_run: bool = False,
        fetch_url_id: int | None = None,
        agency: str | None = None,
    ):
        self.url = url
        self.session = session
        self.source_id = source_id
        self.source_map = source_map or {}
        # station → IANA TZ name. Used by dump_to_db to localize naive
        # timestamps (USBR's per-station local time; wa.gov year-round PST).
        # Unset means naive timestamps get UTC stamped as-is at store time.
        self.source_tz_map = source_tz_map or {}
        self.dry_run = dry_run
        self.fetch_url_id = fetch_url_id
        self.agency = agency
        self._db_updates = 0
        self._obs_buffer: list[dict] = []
        # Cache of ZoneInfo objects to avoid repeated lookups per observation.
        self._tz_cache: dict[str, ZoneInfo] = {}

    # ------------------------------------------------------------------
    # Text feeding (mirrors serveUpLines / serveUpCookedLines)
    # ------------------------------------------------------------------

    def parse(self, text: str) -> int:
        """Feed raw text to the parser, line by line.

        Returns the number of database updates made.
        """
        self._db_updates = 0
        self._obs_buffer = []
        for raw_line in text.splitlines():
            line = raw_line.replace("\r", "")
            if not self.parse_line(line):
                break

        self._flush_buffer()

        if self._db_updates == 0:
            logger.warning("No database updates from %s parser(%s)", self.url, self.name)

        return self._db_updates

    def parse_cooked(self, text: str) -> int:
        """Feed HTML-rendered text (strip tags first), then parse.

        Mirrors serveUpCookedLines() which runs HTMLrender before parsing.
        """
        clean = self._strip_html(text)
        return self.parse(clean)

    @abstractmethod
    def parse_line(self, line: str) -> bool:
        """Process a single line. Return False to stop processing."""
        ...

    # ------------------------------------------------------------------
    # Database helpers (mirrors dumpToDatabase)
    # ------------------------------------------------------------------

    def dump_to_db(
        self,
        station: str,
        data_type: DataType | str,
        when: datetime,
        value: float,
    ) -> bool:
        """Store an observation.

        The station parameter is used for logging/identification but the
        actual DB key is source_id. If source_id is not set (e.g. USGS
        parsers that discover stations dynamically), the station name is
        used for logging only and the observation is stored by source_id.

        If ``when`` is naive and ``source_tz_map`` has an entry for this
        station, the datetime is interpreted in that timezone and converted
        to UTC. This is how per-station local-time feeds (USBR's multi-zone
        CSV, wa.gov year-round PST) get stored correctly without forcing
        parsers to know the TZ themselves.
        """
        # Localize naive timestamps using per-station TZ metadata. Must run
        # BEFORE the debug log so the log reflects the final stored value.
        if when.tzinfo is None:
            tz_name = self.source_tz_map.get(station)
            if tz_name:
                tz = self._tz_cache.get(tz_name)
                if tz is None:
                    tz = ZoneInfo(tz_name)
                    self._tz_cache[tz_name] = tz
                when = when.replace(tzinfo=tz).astimezone(UTC)

        self._db_updates += 1

        logger.debug("DB dump %s/%s %s %s", station, data_type, value, when)

        if self.dry_run:
            return True

        # Resolve source_id: use per-station source_map first, then fall
        # back to the single source_id set at construction time.
        sid = self.source_map.get(station) or self.source_id
        if sid is None:
            if self.fetch_url_id is not None:
                sid = self._auto_create_source(station)
            else:
                logger.error(
                    "No source_id set for %s/%s — cannot store",
                    station,
                    data_type,
                )
                return False

        self._obs_buffer.append(
            {
                "source_id": sid,
                "data_type": data_type,
                "observed_at": when,
                "value": value,
            }
        )
        return True

    def _auto_create_source(self, station: str) -> int:
        """Auto-create a Source record for an unknown station.

        Returns the new source_id and caches it in source_map. Emits a
        warning (not info) because the new row has no ``timezone`` set — if
        this feed publishes local time, observations will be stored as
        naive UTC (shifted by the local offset) until the station is added
        to the URL's ``stations:`` block in data/sources.yaml.
        """
        src = Source(name=station, agency=self.agency, fetch_url_id=self.fetch_url_id)
        self.session.add(src)
        self.session.flush()
        self.source_map[station] = src.id
        logger.warning(
            "Auto-created Source id=%d for station %s (no timezone). "
            "If this feed publishes local time, add it to the stations: block "
            "for this URL in data/sources.yaml.",
            src.id,
            station,
        )
        return src.id

    def _flush_buffer(self) -> None:
        """Flush buffered observations to the database in a single batch."""
        if not self._obs_buffer or self.dry_run:
            self._obs_buffer = []
            return
        neg_flow_sources = get_negative_flow_source_ids(self.session)
        stored = store_observations(
            self.session,
            self._obs_buffer,
            allow_negative_flow_sources=neg_flow_sources,
        )
        if stored < len(self._obs_buffer):
            logger.warning(
                "Stored %d of %d buffered observations for %s",
                stored,
                len(self._obs_buffer),
                self.name,
            )

        # Update latest_observation for each source/type that was stored
        pairs = {(row["source_id"], row["data_type"]) for row in self._obs_buffer}
        for source_id, data_type in pairs:
            update_latest(self.session, source_id, data_type)

        # Update gauge-level cache
        source_to_gauge: dict[int, int] = {}
        source_ids = {row["source_id"] for row in self._obs_buffer}
        for gs in self.session.scalars(
            select(GaugeSource).where(GaugeSource.source_id.in_(source_ids))
        ):
            source_to_gauge[gs.source_id] = gs.gauge_id
        gauge_pairs = {
            (source_to_gauge[sid], dtype) for sid, dtype in pairs if sid in source_to_gauge
        }
        for gauge_id, data_type in gauge_pairs:
            update_latest_gauge(self.session, gauge_id, data_type)

        self._obs_buffer = []

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_html(text: str) -> str:
        """Strip HTML tags and decode entities (mirrors HTMLrender)."""
        clean = _HTML_TAG_RE.sub("", text)
        return html.unescape(clean)
