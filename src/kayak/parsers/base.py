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

from kayak.db.data_db import store_observation
from kayak.db.models import DataType

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
        verbose: bool = False,
        dry_run: bool = False,
    ):
        self.url = url
        self.session = session
        self.source_id = source_id
        self.verbose = verbose
        self.dry_run = dry_run
        self._db_updates = 0

    # ------------------------------------------------------------------
    # Text feeding (mirrors serveUpLines / serveUpCookedLines)
    # ------------------------------------------------------------------

    def parse(self, text: str) -> int:
        """Feed raw text to the parser, line by line.

        Returns the number of database updates made.
        """
        self._db_updates = 0
        for raw_line in text.splitlines():
            line = raw_line.replace("\r", "")
            if not self.parse_line(line):
                break

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

        if self.verbose:
            logger.info(
                "DB dump %s/%s %s %s", station, data_type, value, when
            )

        if self.dry_run:
            return True

        if self.source_id is None:
            logger.error(
                "No source_id set for %s/%s — cannot store", station, data_type,
            )
            return False

        ok = store_observation(self.session, self.source_id, data_type, when, value)
        if not ok:
            logger.error(
                "dumpToDatabase failed for %s/%s %s %s %s %s",
                station, data_type, value, when, self.url, self.name,
            )
        return ok

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
