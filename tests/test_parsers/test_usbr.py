"""Tests for the USBR parser."""

from kayak.db.models import DataType, FetchUrl, Observation, Source
from kayak.parsers.usbr import USBRParser

USBR_FLOW_SAMPLE = """\
Some header text
BEGIN DATA
DATE              TIME  | STN1,Q  | STN2,GH
2024-06-15,       12:00,  1520.0,    8.45
2024-06-15,       13:00,  1480.0,    8.40
END DATA
"""

USBR_GAUGE_SAMPLE = """\
BEGIN DATA
DATE              TIME  | GAUGE1,GH
2024-06-15,       12:00,  8.45
2024-06-15,       13:00,  8.40
END DATA
"""

USBR_TEMP_SAMPLE = """\
BEGIN DATA
DATE              TIME  | TEMP1,WC
2024-06-15,       12:00,  20.0
2024-06-15,       13:00,  25.0
END DATA
"""

USBR_NO_END = """\
BEGIN DATA
DATE              TIME  | STN1,Q
2024-06-15,       12:00,  1520.0
"""


def _make_source(session, name="usbr_test"):
    fu = FetchUrl(url=f"https://example.com/{name}", parser="usbr", is_active=True)
    session.add(fu)
    session.flush()
    src = Source(name=name, fetch_url_id=fu.id)
    session.add(src)
    session.flush()
    return src


class TestUSBRFlow:
    def test_parse_basic_flow(self, session):
        """Parse USBR data with flow (Q) values."""
        src = _make_source(session)
        parser = USBRParser(
            url="https://example.com/usbr", session=session, source_id=src.id
        )
        count = parser.parse(USBR_FLOW_SAMPLE)

        # 2 rows x 2 columns (Q + GH) = 4 updates
        assert count == 4

        flows = (
            session.query(Observation)
            .filter_by(source_id=src.id, data_type=DataType.flow)
            .order_by(Observation.observed_at)
            .all()
        )
        assert len(flows) == 2
        assert flows[0].value == 1520.0
        assert flows[1].value == 1480.0


class TestUSBRGauge:
    def test_parse_gauge_values(self, session):
        """Parse USBR data with gauge (GH) values."""
        src = _make_source(session)
        parser = USBRParser(
            url="https://example.com/usbr", session=session, source_id=src.id
        )
        count = parser.parse(USBR_GAUGE_SAMPLE)

        assert count == 2

        gauges = (
            session.query(Observation)
            .filter_by(source_id=src.id, data_type=DataType.gauge)
            .order_by(Observation.observed_at)
            .all()
        )
        assert len(gauges) == 2
        assert gauges[0].value == 8.45
        assert gauges[1].value == 8.40


class TestUSBRTemperature:
    def test_temperature_celsius_to_fahrenheit(self, session):
        """WC code should convert Celsius to Fahrenheit."""
        src = _make_source(session)
        parser = USBRParser(
            url="https://example.com/usbr", session=session, source_id=src.id
        )
        count = parser.parse(USBR_TEMP_SAMPLE)

        assert count == 2

        temps = (
            session.query(Observation)
            .filter_by(source_id=src.id, data_type=DataType.temperature)
            .order_by(Observation.observed_at)
            .all()
        )
        assert len(temps) == 2
        # 20C = 68.0F, 25C = 77.0F
        assert abs(temps[0].value - 68.0) < 0.2
        assert abs(temps[1].value - 77.0) < 0.2


class TestUSBREdgeCases:
    def test_empty_input(self, session):
        """Empty input should return 0."""
        src = _make_source(session)
        parser = USBRParser(
            url="https://example.com/usbr", session=session, source_id=src.id
        )
        assert parser.parse("") == 0

    def test_no_end_data_still_parses(self, session):
        """BEGIN DATA without END DATA should still parse available rows."""
        src = _make_source(session)
        parser = USBRParser(
            url="https://example.com/usbr", session=session, source_id=src.id
        )
        count = parser.parse(USBR_NO_END)
        assert count == 1

    def test_dry_run(self, session):
        """Dry run should count but not store observations."""
        src = _make_source(session)
        parser = USBRParser(
            url="https://example.com/usbr", session=session, source_id=src.id, dry_run=True
        )
        count = parser.parse(USBR_FLOW_SAMPLE)
        assert count == 4
        assert session.query(Observation).count() == 0
