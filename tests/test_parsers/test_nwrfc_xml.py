"""Tests for the NWRFC XML parser."""

from kayak.db.models import DataType, FetchUrl, Observation, Source
from kayak.parsers.nwrfc_xml import NWRFCXMLParser

NWRFC_BASIC = """\
<?xml version="1.0"?>
<forecast>
  <SiteData id="TESTW">
    <observedData>
      <dataDateTime>2024-06-15T12:00:00</dataDateTime>
      <stage units="feet">3.45</stage>
      <discharge units="cubic feet per second">1520</discharge>
    </observedData>
    <observedData>
      <dataDateTime>2024-06-15T13:00:00</dataDateTime>
      <stage units="feet">3.50</stage>
      <discharge units="cubic feet per second">1540</discharge>
    </observedData>
  </SiteData>
</forecast>
"""

NWRFC_NEGATIVE_FLOW = """\
<?xml version="1.0"?>
<forecast>
  <SiteData id="NEGW">
    <observedData>
      <dataDateTime>2024-06-15T12:00:00</dataDateTime>
      <stage units="feet">3.45</stage>
      <discharge units="cubic feet per second">-100</discharge>
    </observedData>
  </SiteData>
</forecast>
"""

NWRFC_MISSING_ELEMENTS = """\
<?xml version="1.0"?>
<forecast>
  <SiteData id="MISSW">
    <observedData>
      <dataDateTime>2024-06-15T12:00:00</dataDateTime>
    </observedData>
  </SiteData>
</forecast>
"""

NWRFC_FUTURE = """\
<?xml version="1.0"?>
<forecast>
  <SiteData id="FUTW">
    <observedData>
      <dataDateTime>2099-01-01T00:00:00</dataDateTime>
      <stage units="feet">5.00</stage>
      <discharge units="cubic feet per second">2000</discharge>
    </observedData>
  </SiteData>
</forecast>
"""


def _make_source(session, name="nwrfc_test"):
    fu = FetchUrl(url=f"https://example.com/{name}", parser="nwrfc.xml", is_active=True)
    session.add(fu)
    session.flush()
    src = Source(name=name, fetch_url_id=fu.id)
    session.add(src)
    session.flush()
    return src


class TestNWRFCBasic:
    def test_parse_stage_and_discharge(self, session):
        """Parse XML with both stage (gauge) and discharge (flow)."""
        src = _make_source(session)
        parser = NWRFCXMLParser(url="https://example.com/nwrfc", session=session, source_id=src.id)
        count = parser.parse(NWRFC_BASIC)

        # 2 observations x 2 types (gauge + flow) = 4
        assert count == 4

        flows = (
            session.query(Observation)
            .filter_by(source_id=src.id, data_type=DataType.flow)
            .order_by(Observation.observed_at)
            .all()
        )
        assert len(flows) == 2
        assert flows[0].value == 1520.0

        gauges = (
            session.query(Observation)
            .filter_by(source_id=src.id, data_type=DataType.gauge)
            .order_by(Observation.observed_at)
            .all()
        )
        assert len(gauges) == 2
        assert gauges[0].value == 3.45


class TestNWRFCNegativeFlow:
    def test_rejects_negative_flow(self, session):
        """Negative discharge values should be rejected."""
        src = _make_source(session)
        parser = NWRFCXMLParser(url="https://example.com/nwrfc", session=session, source_id=src.id)
        count = parser.parse(NWRFC_NEGATIVE_FLOW)

        # Stage is stored, but negative flow is not
        gauges = (
            session.query(Observation).filter_by(source_id=src.id, data_type=DataType.gauge).all()
        )
        flows = (
            session.query(Observation).filter_by(source_id=src.id, data_type=DataType.flow).all()
        )
        assert len(gauges) == 1
        assert len(flows) == 0
        assert count == 1


class TestNWRFCEdgeCases:
    def test_missing_elements(self, session):
        """observedData without stage or discharge should produce 0 updates."""
        src = _make_source(session)
        parser = NWRFCXMLParser(url="https://example.com/nwrfc", session=session, source_id=src.id)
        count = parser.parse(NWRFC_MISSING_ELEMENTS)
        assert count == 0

    def test_empty_invalid_xml(self, session):
        """Empty or invalid XML should return 0."""
        src = _make_source(session)
        parser = NWRFCXMLParser(url="https://example.com/nwrfc", session=session, source_id=src.id)
        assert parser.parse("") == 0
        assert parser.parse("not xml at all") == 0

    def test_future_timestamps_rejected(self, session):
        """Timestamps far in the future should be rejected."""
        src = _make_source(session)
        parser = NWRFCXMLParser(url="https://example.com/nwrfc", session=session, source_id=src.id)
        count = parser.parse(NWRFC_FUTURE)
        # Future datetime causes when=None, so stage/discharge are skipped
        assert count == 0
