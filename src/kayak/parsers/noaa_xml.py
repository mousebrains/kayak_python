"""NOAA XML parser (replaces Parse_NOAA_XML.C).

Parses XML documents with /observed/datum elements containing
stage (ft), discharge (cfs/kcfs) measurements.
"""

from __future__ import annotations

import logging
import math

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import parse_datetime, safe_float

logger = logging.getLogger(__name__)


@register("noaa.xml")
class NOAAXMLParser(BaseParser):
    name = "noaa.xml"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def parse(self, text: str) -> int:
        """Override parse to use XML processing instead of line-by-line."""
        self._db_updates = 0

        try:
            from lxml import etree
        except ImportError:
            logger.error("lxml required for NOAA XML parser")
            return 0

        try:
            root = etree.fromstring(text.encode("utf-8"))
        except etree.XMLSyntaxError as e:
            logger.error("XML parse error for %s: %s", self.url, e)
            return 0

        # Get station ID from root element
        station_id = root.get("id", "")
        if not station_id:
            # Try to find it in the XML
            name_el = root.find(".//{*}name")
            if name_el is not None and name_el.text:
                station_id = name_el.text
        if not station_id:
            logger.warning("No station ID found in XML for %s", self.url)
            return 0

        # Walk /observed/datum elements
        when = None
        for elem in root.iter():
            tag = etree.QName(elem.tag).localname if "}" in str(elem.tag) else elem.tag
            path = self._elem_path(elem)

            if "observed" in path and "datum" in path:
                tz = elem.get("timezone", "")
                if tz and elem.text:
                    # This is a timestamp element
                    when = parse_datetime(elem.text.strip(), tz)
                elif when and elem.text:
                    # This is a measurement element
                    units = elem.get("units", "")
                    if not units:
                        continue
                    val = safe_float(elem.text.strip())
                    if val is None or not math.isfinite(val):
                        continue

                    if units == "ft":
                        self.dump_to_db(station_id, DataType.GAGE, when, val)
                    elif units == "cfs":
                        if 0 <= val <= 2e6:
                            self.dump_to_db(station_id, DataType.FLOW, when, val)
                    elif units == "kcfs":
                        if 0 <= val <= 2e6:
                            self.dump_to_db(station_id, DataType.FLOW, when, val * 1000)
                    else:
                        logger.warning("Unrecognized units '%s' for %s", units, station_id)

        if self._db_updates == 0:
            logger.warning("No database updates from %s parser(%s)", self.url, self.name)

        return self._db_updates

    def parse_line(self, line: str) -> bool:
        # Not used — XML parsing happens in parse() override
        return True

    @staticmethod
    def _elem_path(elem) -> str:
        """Build a simple path string for element ancestry matching."""
        parts = []
        current = elem
        while current is not None:
            tag = current.tag
            if "}" in str(tag):
                tag = tag.split("}")[1]
            parts.append(str(tag))
            current = current.getparent()
        return "/".join(reversed(parts))
