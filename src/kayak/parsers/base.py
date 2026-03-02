"""Abstract base parser (replaces Parse.C/H).

Each parser processes text fetched from a government agency data source.
The C++ pattern used a virtual ``line()`` method called for each line.
In Python we keep the same pattern but with an ABC.
"""

from __future__ import annotations

import html
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime

from sqlalchemy.orm import Session

from kayak.db.data_db import store_observations
from kayak.db.models import DataType, Source

logger = logging.getLogger(__name__)


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
        dry_run: bool = False,
        fetch_url_id: int | None = None,
        agency: str | None = None,
    ):
        self.url = url
        self.session = session
        self.source_id = source_id
        self.source_map = source_map or {}
        self.dry_run = dry_run
        self.fetch_url_id = fetch_url_id
        self.agency = agency
        self._db_updates = 0
        self._obs_buffer: list[dict] = []

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
            logger.warning(
                "No database updates from %s parser(%s)", self.url, self.name
            )

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
        """
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
                    "No source_id set for %s/%s — cannot store", station, data_type,
                )
                return False

        self._obs_buffer.append({
            "source_id": sid,
            "data_type": data_type,
            "observed_at": when,
            "value": value,
        })
        return True

    def _auto_create_source(self, station: str) -> int:
        """Auto-create a Source record for an unknown station.

        Returns the new source_id and caches it in source_map.
        """
        src = Source(name=station, agency=self.agency, fetch_url_id=self.fetch_url_id)
        self.session.add(src)
        self.session.flush()
        self.source_map[station] = src.id
        logger.info("Auto-created Source id=%d for station %s", src.id, station)
        return src.id

    def _flush_buffer(self) -> None:
        """Flush buffered observations to the database in a single batch."""
        if not self._obs_buffer or self.dry_run:
            self._obs_buffer = []
            return
        stored = store_observations(self.session, self._obs_buffer)
        if stored < len(self._obs_buffer):
            logger.warning(
                "Stored %d of %d buffered observations for %s",
                stored, len(self._obs_buffer), self.name,
            )
        self._obs_buffer = []

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_html(text: str) -> str:
        """Strip HTML tags and decode entities (mirrors HTMLrender)."""
        # Remove tags
        clean = re.sub(r"<[^>]+>", "", text)
        # Decode HTML entities
        clean = html.unescape(clean)
        return clean
