"""Abstract base parser.

Each parser processes text fetched from a government agency data source
by implementing a pure ``parse_records(text) -> list[ObservationRecord]``
method.  The base class's ``parse()`` wraps that with the legacy
``dump_to_db`` → ``_flush_buffer`` path, so subclasses only ever have
to think about text → records.  Three of the six parsers (nwps,
usace.cda, nwrfc.xml) override ``parse()`` to preserve a specific
syntax-error log line; the rest inherit the base wrapper unchanged.
"""

import html
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from kayak.db.cache import update_latest, update_latest_gauge
from kayak.db.models import DataType, GaugeSource, Source
from kayak.db.observations import store_observations
from kayak.db.sources import get_negative_flow_source_ids

logger = logging.getLogger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class ObservationRecord:
    """One parsed observation, pre-DB.

    The four fields every ``dump_to_db`` call already carries. Frozen
    so tests can ``assertEqual`` on lists of records. ``observed_at``
    is normally timezone-aware UTC by the time a parser emits a record
    (naive-timestamp parsers do the localization step inline before
    constructing the record). Parsers whose feeds publish per-station
    local time (USBR, wa.gov) MAY emit naive datetimes when the
    construction-time ``source_tz_map`` lacks the station — those get
    stored as-is and treated as UTC by SQLite.
    """

    station: str
    data_type: DataType
    observed_at: datetime
    value: float


class BaseParser(ABC):
    """Abstract base for all data source parsers.

    Subclasses must implement ``parse_records()`` and set ``name``.
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
    # Pure parsing contract + thin DB wrapper
    # ------------------------------------------------------------------

    @abstractmethod
    def parse_records(self, text: str) -> list["ObservationRecord"]:
        """Pure: feed text → list of records. No session, no DB.

        Implementations should return ``[]`` for malformed input — the
        ``parse()`` wrapper handles any error-log line. Parsers whose
        feeds publish per-station local time may apply
        ``self.source_tz_map`` localization inline; everything else
        should emit tz-aware UTC datetimes.
        """
        ...

    def parse(self, text: str) -> int:
        """Wrap ``parse_records`` with the legacy DB path.

        Resets the per-call counters, runs the pure parse, dumps each
        record through ``dump_to_db`` (which still handles
        per-station tz-map localization for any naive datetime the
        parser passed through), flushes the buffer, and emits the
        "no updates" warning if nothing landed. Returns the number of
        observations counted by ``dump_to_db``.
        """
        self._db_updates = 0
        self._obs_buffer = []
        for r in self.parse_records(text):
            self.dump_to_db(r.station, r.data_type, r.observed_at, r.value)
        self._flush_buffer()
        if self._db_updates == 0:
            logger.warning("No database updates from %s parser(%s)", self.url, self.name)
        return self._db_updates

    # ------------------------------------------------------------------
    # Database helpers (mirrors dumpToDatabase)
    # ------------------------------------------------------------------

    def _localize(self, when: datetime, station: str) -> datetime:
        """Apply ``source_tz_map`` to a naive datetime; pass tz-aware through.

        Naive timestamps from per-station local-time feeds (USBR's multi-zone
        CSV, wa.gov year-round PST) are interpreted in the station's mapped
        timezone and converted to UTC. Without a mapping the datetime stays
        naive (and is treated as UTC at store time). Used both inline by
        ``dump_to_db`` and by parsers that localize while building records.
        """
        if when.tzinfo is not None:
            return when
        tz_name = self.source_tz_map.get(station)
        if not tz_name and len(self.source_tz_map) == 1:
            # Single-source fetch: the parser may key records by a station id
            # that differs from the source's name — e.g. wa.gov sources renamed
            # to per-file stems (29C100_STG_FM) while the parser still reads the
            # bare 29C100 from the file header. With exactly one source tz on the
            # fetch there is no ambiguity, so apply it. Multi-station feeds (USBR)
            # carry >1 entry → this fallback does not fire and per-station tz wins.
            tz_name = next(iter(self.source_tz_map.values()))
        if not tz_name:
            return when
        tz = self._tz_cache.get(tz_name)
        if tz is None:
            tz = ZoneInfo(tz_name)
            self._tz_cache[tz_name] = tz
        return when.replace(tzinfo=tz).astimezone(UTC)

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
        when = self._localize(when, station)

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

        Returns the new source_id and caches it in source_map. Always emits
        the timezone WARNING (the new row has no ``timezone`` set — if this
        feed publishes local time, observations will be stored as naive UTC
        shifted by the local offset until the station is added to the URL's
        ``stations:`` block in data/sources.yaml).

        Additionally escalates to ERROR when ``self.source_map`` is empty
        before the new row goes in: that means the fetch_url has zero other
        live sources, the post-deletion-migration "URL orphaned of sources"
        case (see ``docs/done/PLAN_orphan_sources.md``). The new Source row will
        also be missing a ``gauge_source`` link — Phase 2b's end-of-pipeline
        gate trips on this. Logging here lets the operator find the
        offending URL by grepping the fetch run output. The legitimate
        multi-station case (USGS basin feeds, wa.gov station dirs) leaves
        ``source_map`` non-empty and silences the ERROR.
        """
        url_was_orphaned = not self.source_map
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
        if url_was_orphaned:
            logger.error(
                "ORPHAN auto-create: Source id=%d (station %s, url=%s, "
                "fetch_url_id=%s) — the URL had no other live sources. "
                "Link via gauge_source or remove the URL from sources.yaml. "
                "See docs/done/PLAN_orphan_sources.md.",
                src.id,
                station,
                self.url,
                self.fetch_url_id,
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
