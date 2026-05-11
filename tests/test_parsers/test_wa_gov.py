"""Tests for the Washington State DOE (wa.gov) parser."""

from kayak.db.models import DataType, FetchUrl, Observation, Source
from kayak.parsers.wa_gov import WaGovParser

WA_GOV_TEMP = """\
STATION1--Test Station Description
DATE TIME Water_Temp  Quality
---  ---  -------  -------
06/15/2024 12:00  18.5  100
06/15/2024 13:00  19.0  100
"""

WA_GOV_STAGE = """\
STATION2--Another Station
DATE TIME Stage  Quality
---  ---  -------  -------
06/15/2024 12:00  3.45  100
06/15/2024 13:00  3.50  100
"""

WA_GOV_BAD_QUALITY = """\
STATION3--Bad Quality Station
DATE TIME Water_Temp  Quality
---  ---  -------  -------
06/15/2024 12:00  18.5  0
06/15/2024 13:00  19.0  200
06/15/2024 14:00  20.0  100
"""

WA_GOV_NO_DATA = """\
STATION4--No Data Station
DATE TIME Water_Temp  Quality
---  ---  -------  -------
06/15/2024 12:00  No Data  100
06/15/2024 13:00  18.5  100
"""


def _make_source(session, name="wa_test"):
    fu = FetchUrl(url=f"https://example.com/{name}", parser="wa.gov", is_active=True)
    session.add(fu)
    session.flush()
    src = Source(name=name, fetch_url_id=fu.id)
    session.add(src)
    session.flush()
    return src


class TestWaGovTemperature:
    def test_parse_temperature_c_to_f(self, session):
        """Water_Temp header should trigger temperature type with C-to-F conversion."""
        src = _make_source(session)
        parser = WaGovParser(url="https://example.com/wa", session=session, source_id=src.id)
        count = parser.parse(WA_GOV_TEMP)

        assert count == 2

        temps = (
            session.query(Observation)
            .filter_by(source_id=src.id, data_type=DataType.temperature)
            .order_by(Observation.observed_at)
            .all()
        )
        assert len(temps) == 2
        # 18.5C = 65.3F, 19.0C = 66.2F
        assert abs(temps[0].value - 65.3) < 0.2
        assert abs(temps[1].value - 66.2) < 0.2


class TestWaGovStage:
    def test_parse_stage_gauge(self, session):
        """Stage header should trigger gauge data type."""
        src = _make_source(session)
        parser = WaGovParser(url="https://example.com/wa", session=session, source_id=src.id)
        count = parser.parse(WA_GOV_STAGE)

        assert count == 2

        gauges = (
            session.query(Observation)
            .filter_by(source_id=src.id, data_type=DataType.gauge)
            .order_by(Observation.observed_at)
            .all()
        )
        assert len(gauges) == 2
        assert gauges[0].value == 3.45
        assert gauges[1].value == 3.50


class TestWaGovQuality:
    def test_quality_filtering(self, session):
        """Quality <= 0 or >= 200 should be rejected."""
        src = _make_source(session)
        parser = WaGovParser(url="https://example.com/wa", session=session, source_id=src.id)
        count = parser.parse(WA_GOV_BAD_QUALITY)

        # Only the row with quality=100 passes (14:00 row)
        assert count == 1

        temps = session.query(Observation).filter_by(source_id=src.id).all()
        assert len(temps) == 1


class TestWaGovQualityBoundaries:
    """Explicit boundary tests for the quality code filter."""

    def _parse_single(self, session, quality_value):
        src = _make_source(session)
        parser = WaGovParser(url="https://example.com/wa", session=session, source_id=src.id)
        text = (
            f"BNDRY--Boundary Test\n"
            f"DATE TIME Water_Temp  Quality\n"
            f"---  ---  -------  -------\n"
            f"06/15/2024 12:00  18.5  {quality_value}\n"
        )
        return parser.parse(text)

    def test_quality_zero_rejected(self, session):
        assert self._parse_single(session, 0) == 0

    def test_quality_one_accepted(self, session):
        assert self._parse_single(session, 1) == 1

    def test_quality_199_accepted(self, session):
        assert self._parse_single(session, 199) == 1

    def test_quality_200_rejected(self, session):
        assert self._parse_single(session, 200) == 0


class TestWaGovEdgeCases:
    def test_no_data_lines_skipped(self, session):
        """Lines containing 'No Data' should be skipped."""
        src = _make_source(session)
        parser = WaGovParser(url="https://example.com/wa", session=session, source_id=src.id)
        count = parser.parse(WA_GOV_NO_DATA)

        # Only the 13:00 row with actual data passes
        assert count == 1

    def test_empty_input(self, session):
        """Empty input should return 0."""
        src = _make_source(session)
        parser = WaGovParser(url="https://example.com/wa", session=session, source_id=src.id)
        assert parser.parse("") == 0

    def test_html_error_page(self, session):
        """Server-returned HTML error page must not crash the line parser."""
        src = _make_source(session)
        parser = WaGovParser(url="https://example.com/wa", session=session, source_id=src.id)
        html = (
            "<!doctype html><html><body><h1>502 Bad Gateway</h1>"
            "<p>The proxy server failed to respond.</p></body></html>"
        )
        assert parser.parse(html) == 0

    def test_garbage_lines_skipped(self, session):
        """Random non-data lines must be skipped, not crashed on."""
        src = _make_source(session)
        parser = WaGovParser(url="https://example.com/wa", session=session, source_id=src.id)
        garbage = "Some Header Line\ngarbage,without,enough,fields\n,,,,\n*** TRUNCATED ***\n"
        assert parser.parse(garbage) == 0
