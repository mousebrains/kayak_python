"""NWRFC XML parser.

Format: XML with nested SiteData/observedData/observedValue.
Extracts stage (feet), discharge (cfs), and inflow (cfs).
"""

import logging
import math
from datetime import UTC, datetime
from typing import Any

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser, ObservationRecord
from kayak.parsers.registry import register
from kayak.utils.conversions import parse_datetime, safe_float

logger = logging.getLogger(__name__)

# Tag → (data_type, units-attr-default, valid unit substrings, require non-negative).
# Keeps the per-tag dispatch table-driven so adding/removing a tag is a one-row
# change instead of an elif edit.
_TAG_HANDLERS: dict[str, tuple[DataType, str, tuple[str, ...], bool]] = {
    "stage": (DataType.gauge, "feet", ("feet", "ft"), False),
    "discharge": (DataType.flow, "", ("cubic", "cfs"), False),
    "inflow": (DataType.inflow, "", ("cubic", "cfs"), True),
}


@register("nwrfc.xml")
class NWRFCXMLParser(BaseParser):
    """NW River Forecast Center XML parser.

    Parses observed-data XML from NWRFC. Extracts stage (ft) and flow (kcfs)
    values, converting kcfs to cfs. Rejects negative flow values. Uses lxml
    for XML parsing rather than line-by-line processing.
    """

    name = "nwrfc.xml"

    def parse_records(
        self,
        text: str,
        *,
        now: datetime | None = None,
    ) -> list[ObservationRecord]:
        """Pure: XML → records. No session, no DB.

        Returns ``[]`` on lxml import-error or XML-syntax-error — the
        wrapper ``parse()`` handles the logging.
        """
        try:
            from lxml import etree
        except ImportError:
            return []

        # Disable entity resolution, network access, and DTD loading to
        # block XXE and billion-laughs attacks. Inbound XML comes over
        # TLS but we defend in depth.
        xml_parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            load_dtd=False,
            huge_tree=False,
        )
        try:
            root = etree.fromstring(text.encode("utf-8"), xml_parser)
        except etree.XMLSyntaxError:
            return []

        if now is None:
            now = datetime.now(UTC)

        records: list[ObservationRecord] = []
        for site in root.iter():
            tag = self._local_tag(site)
            if tag in ("SiteData", "siteData"):
                station = site.get("id", "")
                if not station:
                    for child in site:
                        if self._local_tag(child) in ("siteId", "id"):
                            station = (child.text or "").strip()
                            break
                self._collect_site(site, station, now, records)
        return records

    def _collect_site(
        self,
        site_elem: Any,
        station: str,
        now: datetime,
        records: list[ObservationRecord],
    ) -> None:
        for elem in site_elem.iter():
            tag = self._local_tag(elem)
            if tag in ("observedData", "observed"):
                self._collect_observed(elem, station, now, records)

    def _collect_observed(
        self,
        observed_elem: Any,
        station: str,
        now: datetime,
        records: list[ObservationRecord],
    ) -> None:
        when: datetime | None = None
        for elem in observed_elem.iter():
            tag = self._local_tag(elem)
            if tag == "dataDateTime":
                text = (elem.text or "").strip()
                if text:
                    when = self._parse_when(text, now)
                continue
            if when is None:
                continue
            handler = _TAG_HANDLERS.get(tag)
            if handler is not None:
                record = self._record_from_elem(station, when, elem, *handler)
                if record is not None:
                    records.append(record)

    @staticmethod
    def _parse_when(text: str, now: datetime) -> datetime | None:
        """Parse a dataDateTime payload; reject future timestamps."""
        when = parse_datetime(text)
        if when is not None and when > now:
            return None
        return when

    @staticmethod
    def _record_from_elem(
        station: str,
        when: datetime,
        elem: Any,
        data_type: DataType,
        units_default: str,
        valid_unit_substrings: tuple[str, ...],
        require_non_negative: bool,
    ) -> ObservationRecord | None:
        """Build a record if `elem` carries a finite, unit-compatible value."""
        text = (elem.text or "").strip()
        if not text:
            return None
        units = elem.get("units", units_default).lower()
        if not any(s in units for s in valid_unit_substrings):
            return None
        val = safe_float(text)
        if val is None or not math.isfinite(val):
            return None
        if require_non_negative and val < 0:
            return None
        return ObservationRecord(station, data_type, when, val)

    def parse(self, text: str) -> int:
        """Override to keep the lxml-missing / XML-syntax error logs.

        ``parse_records`` silently returns ``[]`` for both cases; this
        wrapper re-runs the XML parse once with URL context so the
        error log fires before delegating to ``super().parse()``.
        """
        try:
            from lxml import etree
        except ImportError:
            logger.error("lxml required for NWRFC XML parser")
            return 0
        xml_parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            load_dtd=False,
            huge_tree=False,
        )
        try:
            etree.fromstring(text.encode("utf-8"), xml_parser)
        except etree.XMLSyntaxError as e:
            logger.error("XML parse error for %s: %s", self.url, e)
            return 0
        return super().parse(text)

    @staticmethod
    def _local_tag(elem: Any) -> str:
        tag = str(elem.tag)
        return tag.split("}")[-1] if "}" in tag else tag
