"""Tests for the USBR parser."""

from kayak.db.models import DataType, FetchUrl, Observation, Source
from kayak.parsers.usbr import USBRParser

USBR_FLOW_SAMPLE = """\
DateTime,stn1_q,stn2_gh
06/15/2024 12:00,1520.0,8.45
06/15/2024 13:00,1480.0,8.40
"""

USBR_GAUGE_SAMPLE = """\
DateTime,gauge1_gh
06/15/2024 12:00,8.45
06/15/2024 13:00,8.40
"""

USBR_TEMP_SAMPLE = """\
DateTime,temp1_wc
06/15/2024 12:00,20.0
06/15/2024 13:00,25.0
"""

USBR_HTML_WRAPPED = """\
<HTML><BODY><PRE>
DateTime,stn1_q
06/15/2024 12:00,1520.0
</PRE></BODY></HTML>
"""

USBR_MULTI_STATION = """\
DateTime,mado_gh,mado_q,mado_wf,waro_gh,waro_q
06/15/2024 12:00,3.48,196.00,43.40,3.32,0.13
06/15/2024 13:00,3.47,194.00,43.30,3.31,0.12
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
        """Parse USBR CSV with flow (Q) and gauge (GH) values."""
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
        """Parse USBR CSV with gauge (GH) values."""
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

    def test_html_wrapped(self, session):
        """HTML-wrapped response should still parse correctly."""
        src = _make_source(session)
        parser = USBRParser(
            url="https://example.com/usbr", session=session, source_id=src.id
        )
        count = parser.parse(USBR_HTML_WRAPPED)
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

    def test_multi_station_source_map(self, session):
        """Multi-station CSV uses source_map for per-station lookup."""
        fu = FetchUrl(url="https://example.com/multi", parser="usbr", is_active=True)
        session.add(fu)
        session.flush()
        src_mado = Source(name="MADO", fetch_url_id=fu.id)
        src_waro = Source(name="WARO", fetch_url_id=fu.id)
        session.add_all([src_mado, src_waro])
        session.flush()

        source_map = {"MADO": src_mado.id, "WARO": src_waro.id}
        parser = USBRParser(
            url="https://example.com/multi", session=session, source_map=source_map
        )
        count = parser.parse(USBR_MULTI_STATION)

        # MADO: 2 rows x 3 cols (gh, q, wf) = 6
        # WARO: 2 rows x 2 cols (gh, q) = 4
        assert count == 10

        mado_flows = (
            session.query(Observation)
            .filter_by(source_id=src_mado.id, data_type=DataType.flow)
            .all()
        )
        assert len(mado_flows) == 2
        assert mado_flows[0].value == 196.0

        waro_gauges = (
            session.query(Observation)
            .filter_by(source_id=src_waro.id, data_type=DataType.gauge)
            .all()
        )
        assert len(waro_gauges) == 2
