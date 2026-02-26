"""Tests for USGS parser."""

from pathlib import Path

from kayak.db.models import DataType, Measurement
from kayak.parsers.usgs import USGSParser

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_usgs_parser_basic(session):
    """Parse sample USGS RDB data and verify measurements stored."""
    text = (FIXTURES / "usgs_sample.rdb").read_text()

    parser = USGSParser(url="https://example.com/usgs", session=session)
    count = parser.parse(text)

    # 3 rows x 2 params (flow + gage) = 6 updates
    assert count == 6

    # Check flow measurements
    flows = (
        session.query(Measurement)
        .filter_by(station="14321000", data_type=DataType.FLOW)
        .order_by(Measurement.time)
        .all()
    )
    assert len(flows) == 3
    assert flows[0].value == 1520.0
    assert flows[1].value == 1530.0
    assert flows[2].value == 1525.0

    # Check gage measurements
    gages = (
        session.query(Measurement)
        .filter_by(station="14321000", data_type=DataType.GAGE)
        .order_by(Measurement.time)
        .all()
    )
    assert len(gages) == 3
    assert gages[0].value == 3.45
    assert gages[1].value == 3.46


def test_usgs_parser_dry_run(session):
    """Dry run should count updates but not store them."""
    text = (FIXTURES / "usgs_sample.rdb").read_text()

    parser = USGSParser(
        url="https://example.com/usgs", session=session, dry_run=True
    )
    count = parser.parse(text)

    assert count == 6
    assert session.query(Measurement).count() == 0


def test_usgs_parser_empty_input(session):
    """Empty input should produce zero updates."""
    parser = USGSParser(url="https://example.com/usgs", session=session)
    count = parser.parse("")
    assert count == 0


def test_usgs_parser_comments_only(session):
    """Input with only comments should produce zero updates."""
    text = "# This is a comment\n# Another comment\n"
    parser = USGSParser(url="https://example.com/usgs", session=session)
    count = parser.parse(text)
    assert count == 0


def test_usgs_temperature_conversion(session):
    """Celsius temperature (param 10) should be converted to Fahrenheit."""
    text = (
        "agency_cd\tsite_no\tdatetime\ttz_cd\t01_00010\t01_00010_cd\n"
        "5s\t15s\t20d\t6s\t14n\t10s\n"
        "USGS\t99999999\t2024-06-15 12:00\tUTC\t20.0\tP\n"
    )
    parser = USGSParser(url="https://example.com/usgs", session=session)
    count = parser.parse(text)

    assert count == 1
    row = session.query(Measurement).first()
    assert row.data_type == DataType.TEMPERATURE
    # 20°C = 68°F
    assert abs(row.value - 68.0) < 0.2
