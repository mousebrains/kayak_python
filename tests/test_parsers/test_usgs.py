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


def test_usgs_cms_codes_converted_to_cfs(session):
    """Param codes 30208/30209/99060 (m³/s) are converted to cfs on ingest.

    Previously these codes were silently dropped because their _Parameter
    entry had no ``db_key``. They now land in DataType.flow with the
    35.3147 m³/s -> cfs multiplier applied.

    Using three different data rows so the (source_id, observed_at,
    data_type) composite PK doesn't collapse all three into one row.
    """
    src = _make_source(session, "cms_test")
    # 1 m³/s -> ~35.3147 cfs. Each CMS code exercised via its own row.
    text = (
        "agency_cd\tsite_no\tdatetime\ttz_cd\t01_30208\t01_30208_cd\t"
        "02_30209\t02_30209_cd\t03_99060\t03_99060_cd\n"
        "5s\t15s\t20d\t6s\t14n\t10s\t14n\t10s\t14n\t10s\n"
        "USGS\t99999999\t2024-06-15 12:00\tUTC\t1.0\tA\t\t\t\t\n"
        "USGS\t99999999\t2024-06-15 12:15\tUTC\t\t\t2.0\tA\t\t\n"
        "USGS\t99999999\t2024-06-15 12:30\tUTC\t\t\t\t\t3.0\tA\n"
    )
    parser = USGSParser(url="https://example.com/usgs", session=session, source_id=src.id)
    count = parser.parse(text)

    assert count == 3, f"expected 3 updates, got {count}"
    flows = (
        session.query(Observation)
        .filter_by(data_type=DataType.flow)
        .order_by(Observation.observed_at)
        .all()
    )
    assert len(flows) == 3
    # 1, 2, 3 m³/s * 35.3147 -> 35.3147, 70.6294, 105.9441
    values = [o.value for o in flows]
    assert abs(values[0] - 35.3147) < 0.01
    assert abs(values[1] - 70.6294) < 0.01
    assert abs(values[2] - 105.9441) < 0.01
