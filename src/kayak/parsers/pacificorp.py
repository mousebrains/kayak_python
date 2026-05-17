"""PacifiCorp hydro XML parser.

Single feed today: Rogue River bypass below North Fork Diversion Dam,
recorded at the OR Hwy 62 bridge gauge ~0.3 mi below the dam.

Format: ``<Measurements>/<Measurement>/<MeasurementValue>`` with naive
local timestamps (America/Los_Angeles). Localization is handled by
``BaseParser.dump_to_db`` via ``source_tz_map`` (set from sources.yaml's
``stations:`` block).
"""

import logging
import math
from typing import Any

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser, ObservationRecord
from kayak.parsers.registry import register
from kayak.utils.conversions import parse_datetime, safe_float

logger = logging.getLogger(__name__)

# PacifiCorp ships "csf" (typo) for cubic-feet-per-second. Accept both.
_FLOW_UNITS = ("cfs", "csf")


@register("pacificorp")
class PacifiCorpParser(BaseParser):
    """PacifiCorp hydro XML parser (cfs flow, hourly, 7-day window)."""

    name = "pacificorp"

    def parse_records(self, text: str) -> list[ObservationRecord]:
        try:
            from lxml import etree
        except ImportError:
            return []

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

        records: list[ObservationRecord] = []
        for meas in root.iter("Measurement"):
            station = self._text(meas, "PowerSystemResourceId")
            if not station:
                continue
            units = self._text(meas, "MeasurementUnit").lower()
            if units not in _FLOW_UNITS:
                continue
            for mv in meas.iter("MeasurementValue"):
                rec = self._record_from_value(mv, station)
                if rec is not None:
                    records.append(rec)
        return records

    @staticmethod
    def _text(parent: Any, tag: str) -> str:
        child = parent.find(tag)
        if child is None or child.text is None:
            return ""
        return str(child.text).strip()

    @staticmethod
    def _record_from_value(mv: Any, station: str) -> ObservationRecord | None:
        # validity==0 is good; -248 pairs with value="n/a" for the in-progress hour.
        q = mv.find("MeasurementValueQuality/validity")
        if q is None or (q.text or "").strip() != "0":
            return None
        ts_text = (mv.findtext("timeStamp") or "").strip()
        val_text = (mv.findtext("value") or "").strip()
        if not ts_text or not val_text:
            return None
        when = parse_datetime(ts_text, assume_naive=True)
        if when is None:
            return None
        val = safe_float(val_text)
        if val is None or not math.isfinite(val) or val < 0:
            return None
        return ObservationRecord(station, DataType.flow, when, val)

    def parse(self, text: str) -> int:
        """Surface lxml-missing / XML-syntax errors before delegating."""
        try:
            from lxml import etree
        except ImportError:
            logger.error("lxml required for PacifiCorp parser")
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
