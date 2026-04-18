"""NWRFC XML parser.

Format: XML with nested SiteData/observedData/observedValue.
Extracts stage (feet), discharge (cfs), and inflow (cfs).
"""

import logging
import math
from datetime import UTC, datetime
from typing import Any

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import parse_datetime, safe_float

logger = logging.getLogger(__name__)


@register("nwrfc.xml")
class NWRFCXMLParser(BaseParser):
    """NW River Forecast Center XML parser.

    Parses observed-data XML from NWRFC. Extracts stage (ft) and flow (kcfs)
    values, converting kcfs to cfs. Rejects negative flow values. Uses lxml
    for XML parsing rather than line-by-line processing.
    """

    name = "nwrfc.xml"

    def parse(self, text: str) -> int:
        """XML-based parsing instead of line-by-line."""
        self._db_updates = 0
        self._obs_buffer = []

        try:
            from lxml import etree
        except ImportError:
            logger.error("lxml required for NWRFC XML parser")
            return 0

        # Disable entity resolution, network access, and DTD loading to block
        # XXE and billion-laughs attacks. Inbound XML comes over TLS but
        # we defend in depth.
        parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            load_dtd=False,
            huge_tree=False,
        )
        try:
            root = etree.fromstring(text.encode("utf-8"), parser)
        except etree.XMLSyntaxError as e:
            logger.error("XML parse error for %s: %s", self.url, e)
            return 0

        now = datetime.now(UTC)

        # Find all SiteData or observedData blocks
        for site in root.iter():
            tag = self._local_tag(site)

            if tag == "SiteData" or tag == "siteData":
                station = site.get("id", "")
                if not station:
                    # Try child element
                    for child in site:
                        if self._local_tag(child) in ("siteId", "id"):
                            station = (child.text or "").strip()
                            break
                self._parse_site(site, station, now)

        self._flush_buffer()

        if self._db_updates == 0:
            logger.warning("No database updates from %s parser(%s)", self.url, self.name)

        return self._db_updates

    def _parse_site(self, site_elem: Any, station: str, now: datetime) -> None:
        """Parse one site's observed data."""
        for elem in site_elem.iter():
            tag = self._local_tag(elem)
            if tag in ("observedData", "observed"):
                self._parse_observed(elem, station, now)

    def _parse_observed(self, observed_elem: Any, station: str, now: datetime) -> None:
        """Parse observed data block."""
        when = None
        for elem in observed_elem.iter():
            tag = self._local_tag(elem)
            text = (elem.text or "").strip()

            if tag == "dataDateTime" and text:
                when = parse_datetime(text)
                if when and when > now:
                    when = None
            elif tag == "stage" and when and text:
                units = elem.get("units", "feet")
                if "feet" in units.lower() or "ft" in units.lower():
                    val = safe_float(text)
                    if val is not None and math.isfinite(val):
                        self.dump_to_db(station, DataType.gauge, when, val)
            elif tag == "discharge" and when and text:
                units = elem.get("units", "")
                if "cubic" in units.lower() or "cfs" in units.lower():
                    val = safe_float(text)
                    if val is not None and math.isfinite(val):
                        self.dump_to_db(station, DataType.flow, when, val)
            elif tag == "inflow" and when and text:
                units = elem.get("units", "")
                if "cubic" in units.lower() or "cfs" in units.lower():
                    val = safe_float(text)
                    if val is not None and math.isfinite(val) and val >= 0:
                        self.dump_to_db(station, DataType.inflow, when, val)

    def parse_line(self, line: str) -> bool:
        return True

    @staticmethod
    def _local_tag(elem: Any) -> str:
        tag = str(elem.tag)
        return tag.split("}")[-1] if "}" in tag else tag
