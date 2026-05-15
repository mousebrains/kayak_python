"""Session-free tests for NWRFCXMLParser.parse_records (T3.1)."""

from __future__ import annotations

from datetime import UTC, datetime

from kayak.db.models import DataType
from kayak.parsers.base import ObservationRecord
from kayak.parsers.nwrfc_xml import NWRFCXMLParser

_PINNED_NOW = datetime(2026, 6, 15, 14, 0, 0, tzinfo=UTC)


def _new_parser() -> NWRFCXMLParser:
    return NWRFCXMLParser(url="https://example.com/nwrfc", session=None)  # type: ignore[arg-type]


def test_parse_records_stage_and_discharge():
    payload = """<?xml version="1.0"?>
<forecast>
  <SiteData id="TESTW">
    <observedData>
      <dataDateTime>2026-06-15T12:00:00</dataDateTime>
      <stage units="feet">3.45</stage>
      <discharge units="cubic feet per second">1520</discharge>
    </observedData>
  </SiteData>
</forecast>
"""
    records = _new_parser().parse_records(payload, now=_PINNED_NOW)
    assert records == [
        ObservationRecord("TESTW", DataType.gauge, datetime(2026, 6, 15, 12, 0, tzinfo=UTC), 3.45),
        ObservationRecord("TESTW", DataType.flow, datetime(2026, 6, 15, 12, 0, tzinfo=UTC), 1520.0),
    ]


def test_parse_records_negative_inflow_dropped():
    """Inflow has require_non_negative=True; negative values skip."""
    payload = """<?xml version="1.0"?>
<forecast>
  <SiteData id="X">
    <observedData>
      <dataDateTime>2026-06-15T12:00:00</dataDateTime>
      <inflow units="cfs">-100</inflow>
    </observedData>
  </SiteData>
</forecast>
"""
    assert _new_parser().parse_records(payload, now=_PINNED_NOW) == []


def test_parse_records_unknown_units_dropped():
    """`discharge units="meters"` fails the cubic/cfs substring check."""
    payload = """<?xml version="1.0"?>
<forecast>
  <SiteData id="X">
    <observedData>
      <dataDateTime>2026-06-15T12:00:00</dataDateTime>
      <discharge units="meters">1000</discharge>
    </observedData>
  </SiteData>
</forecast>
"""
    assert _new_parser().parse_records(payload, now=_PINNED_NOW) == []


def test_parse_records_future_timestamps_dropped():
    payload = """<?xml version="1.0"?>
<forecast>
  <SiteData id="X">
    <observedData>
      <dataDateTime>2099-01-01T00:00:00</dataDateTime>
      <discharge units="cfs">1000</discharge>
    </observedData>
  </SiteData>
</forecast>
"""
    assert _new_parser().parse_records(payload, now=_PINNED_NOW) == []


def test_parse_records_returns_empty_on_xml_syntax_error():
    assert _new_parser().parse_records("not xml", now=_PINNED_NOW) == []
    assert _new_parser().parse_records("", now=_PINNED_NOW) == []
