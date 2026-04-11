"""Tests for USGS parser."""

from pathlib import Path

from kayak.db.models import DataType, FetchUrl, Observation, Source
from kayak.parsers.usgs import USGSParser

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _make_source(session, name="usgs_test"):
    """Create a Source for parser testing."""
    fu = FetchUrl(url=f"https://example.com/{name}", parser="usgs", is_active=True)
    session.add(fu)
    session.flush()
    src = Source(name=name, fetch_url_id=fu.id)
    session.add(src)
    session.flush()
    return src


def test_usgs_parser_basic(session):
    """Parse sample USGS RDB data and verify observations stored."""
    src = _make_source(session)
    text = (FIXTURES / "usgs_sample.rdb").read_text()

    parser = USGSParser(url="https://example.com/usgs", session=session, source_id=src.id)
    count = parser.parse(text)

    # 3 rows x 2 params (flow + gauge) = 6 updates
    assert count == 6

    # Check flow observations
    flows = (
        session.query(Observation)
        .filter_by(source_id=src.id, data_type=DataType.flow)
        .order_by(Observation.observed_at)
        .all()
    )
    assert len(flows) == 3
    assert flows[0].value == 1520.0
    assert flows[1].value == 1530.0
    assert flows[2].value == 1525.0

    # Check gauge observations
    gauges = (
        session.query(Observation)
        .filter_by(source_id=src.id, data_type=DataType.gauge)
        .order_by(Observation.observed_at)
        .all()
    )
    assert len(gauges) == 3
    assert gauges[0].value == 3.45
    assert gauges[1].value == 3.46


def test_usgs_parser_dry_run(session):
    """Dry run should count updates but not store them."""
    src = _make_source(session)
    text = (FIXTURES / "usgs_sample.rdb").read_text()

    parser = USGSParser(
        url="https://example.com/usgs", session=session, source_id=src.id, dry_run=True
    )
    count = parser.parse(text)

    assert count == 6
    assert session.query(Observation).count() == 0


def test_usgs_parser_empty_input(session):
    """Empty input should produce zero updates."""
    src = _make_source(session)
    parser = USGSParser(url="https://example.com/usgs", session=session, source_id=src.id)
    count = parser.parse("")
    assert count == 0


def test_usgs_parser_comments_only(session):
    """Input with only comments should produce zero updates."""
    src = _make_source(session)
    text = "# This is a comment\n# Another comment\n"
    parser = USGSParser(url="https://example.com/usgs", session=session, source_id=src.id)
    count = parser.parse(text)
    assert count == 0


def test_usgs_temperature_conversion(session):
    """Celsius temperature (param 10) should be converted to Fahrenheit."""
    src = _make_source(session, "temp_test")
    text = (
        "agency_cd\tsite_no\tdatetime\ttz_cd\t01_00010\t01_00010_cd\n"
        "5s\t15s\t20d\t6s\t14n\t10s\n"
        "USGS\t99999999\t2024-06-15 12:00\tUTC\t20.0\tP\n"
    )
    parser = USGSParser(url="https://example.com/usgs", session=session, source_id=src.id)
    count = parser.parse(text)

    assert count == 1
    row = session.query(Observation).first()
    assert row.data_type == DataType.temperature
    # 20°C = 68°F
    assert abs(row.value - 68.0) < 0.2
