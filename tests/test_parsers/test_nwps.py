"""Tests for the NWPS API parser."""

import json
from datetime import UTC, datetime, timedelta

from kayak.db.models import DataType, FetchUrl, Observation, Source
from kayak.parsers.nwps import NWPSParser


def _recent(hours_ago=1):
    """Return an ISO timestamp for *hours_ago* hours before now."""
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


NWPS_BASIC = json.dumps(
    {
        "primaryName": "Stage",
        "primaryUnits": "ft",
        "secondaryName": "Flow",
        "secondaryUnits": "kcfs",
        "data": [
            {"validTime": _recent(2), "primary": 7.57, "secondary": 1.52},
            {"validTime": _recent(1), "primary": 7.60, "secondary": 1.54},
        ],
    }
)

NWPS_MISSING_SECONDARY = json.dumps(
    {
        "primaryName": "Stage",
        "primaryUnits": "ft",
        "secondaryName": "Flow",
        "secondaryUnits": "kcfs",
        "data": [
            {"validTime": _recent(1), "primary": 7.57, "secondary": -999},
        ],
    }
)

NWPS_NEGATIVE_FLOW = json.dumps(
    {
        "primaryName": "Stage",
        "primaryUnits": "ft",
        "secondaryName": "Flow",
        "secondaryUnits": "kcfs",
        "data": [
            {"validTime": _recent(1), "primary": 7.57, "secondary": -0.5},
        ],
    }
)

NWPS_FUTURE = json.dumps(
    {
        "primaryName": "Stage",
        "primaryUnits": "ft",
        "secondaryName": "Flow",
        "secondaryUnits": "kcfs",
        "data": [
            {"validTime": "2099-01-01T00:00:00Z", "primary": 5.00, "secondary": 2.0},
        ],
    }
)

NWPS_EMPTY_DATA = json.dumps(
    {
        "primaryName": "Stage",
        "primaryUnits": "ft",
        "secondaryName": "Flow",
        "secondaryUnits": "kcfs",
        "data": [],
    }
)

NWPS_CFS_UNITS = json.dumps(
    {
        "primaryName": "Stage",
        "primaryUnits": "ft",
        "secondaryName": "Flow",
        "secondaryUnits": "cfs",
        "data": [
            {"validTime": _recent(1), "primary": 3.0, "secondary": 500.0},
        ],
    }
)

NWPS_URL = "https://api.water.noaa.gov/nwps/v1/gauges/PRTO3/stageflow/observed"


def _make_source(session, name="nwps_test"):
    fu = FetchUrl(url=f"https://example.com/{name}", parser="nwps", is_active=True)
    session.add(fu)
    session.flush()
    src = Source(name=name, fetch_url_id=fu.id)
    session.add(src)
    session.flush()
    return src


class TestNWPSBasic:
    def test_parse_stage_and_flow(self, session):
        """Parse JSON with both stage and flow; flow converted from kcfs to cfs."""
        src = _make_source(session)
        parser = NWPSParser(url=NWPS_URL, session=session, source_id=src.id)
        count = parser.parse(NWPS_BASIC)

        # 2 observations x 2 types (gauge + flow) = 4
        assert count == 4

        gauges = (
            session.query(Observation)
            .filter_by(source_id=src.id, data_type=DataType.gauge)
            .order_by(Observation.observed_at)
            .all()
        )
        assert len(gauges) == 2
        assert gauges[0].value == 7.57
        assert gauges[1].value == 7.60

        flows = (
            session.query(Observation)
            .filter_by(source_id=src.id, data_type=DataType.flow)
            .order_by(Observation.observed_at)
            .all()
        )
        assert len(flows) == 2
        # 1.52 kcfs * 1000 = 1520 cfs
        assert flows[0].value == 1520.0
        assert flows[1].value == 1540.0

    def test_station_extracted_from_url(self, session):
        """Station LID is extracted from the URL path."""
        src = _make_source(session)
        parser = NWPSParser(url=NWPS_URL, session=session, source_id=src.id)
        assert parser._extract_station(NWPS_URL) == "PRTO3"


class TestNWPSSentinelValues:
    def test_secondary_minus_999_skipped(self, session):
        """secondary = -999 should not be stored; primary should still be stored."""
        src = _make_source(session)
        parser = NWPSParser(url=NWPS_URL, session=session, source_id=src.id)
        count = parser.parse(NWPS_MISSING_SECONDARY)

        # Only stage stored, flow skipped
        assert count == 1

        gauges = (
            session.query(Observation).filter_by(source_id=src.id, data_type=DataType.gauge).all()
        )
        flows = (
            session.query(Observation).filter_by(source_id=src.id, data_type=DataType.flow).all()
        )
        assert len(gauges) == 1
        assert len(flows) == 0

    def test_negative_flow_rejected(self, session):
        """Negative flow values (other than -999 sentinel) should be rejected."""
        src = _make_source(session)
        parser = NWPSParser(url=NWPS_URL, session=session, source_id=src.id)
        count = parser.parse(NWPS_NEGATIVE_FLOW)

        # Stage stored, negative flow rejected
        assert count == 1
        flows = (
            session.query(Observation).filter_by(source_id=src.id, data_type=DataType.flow).all()
        )
        assert len(flows) == 0


class TestNWPSEdgeCases:
    def test_future_timestamps_rejected(self, session):
        """Timestamps in the future should be skipped."""
        src = _make_source(session)
        parser = NWPSParser(url=NWPS_URL, session=session, source_id=src.id)
        count = parser.parse(NWPS_FUTURE)
        assert count == 0

    def test_empty_invalid_json(self, session):
        """Empty string and non-JSON should return 0."""
        src = _make_source(session)
        parser = NWPSParser(url=NWPS_URL, session=session, source_id=src.id)
        assert parser.parse("") == 0
        assert parser.parse("not json at all") == 0

    def test_empty_data_array(self, session):
        """Valid JSON but empty data array should return 0."""
        src = _make_source(session)
        parser = NWPSParser(url=NWPS_URL, session=session, source_id=src.id)
        count = parser.parse(NWPS_EMPTY_DATA)
        assert count == 0

    def test_cfs_units_no_conversion(self, session):
        """When secondaryUnits is 'cfs', flow should not be multiplied by 1000."""
        src = _make_source(session)
        parser = NWPSParser(url=NWPS_URL, session=session, source_id=src.id)
        count = parser.parse(NWPS_CFS_UNITS)

        assert count == 2
        flows = (
            session.query(Observation).filter_by(source_id=src.id, data_type=DataType.flow).all()
        )
        assert len(flows) == 1
        assert flows[0].value == 500.0  # No conversion


class TestNWPSMalformed:
    """Edge cases that exercise garbage-in robustness — feeds occasionally
    return Cloudflare error pages, half-written JSON, or non-finite floats
    when an upstream sensor glitches. None of these should crash the parser
    or pollute the DB."""

    def test_html_error_page(self, session):
        """An upstream HTML error page (e.g. 502 from a CDN) is not JSON."""
        src = _make_source(session)
        parser = NWPSParser(url=NWPS_URL, session=session, source_id=src.id)
        html = (
            "<!doctype html><html><body><h1>502 Bad Gateway</h1><p>nginx/1.18.0</p></body></html>"
        )
        assert parser.parse(html) == 0

    def test_truncated_json(self, session):
        """Connection dropped mid-stream produces a truncated body."""
        src = _make_source(session)
        parser = NWPSParser(url=NWPS_URL, session=session, source_id=src.id)
        truncated = '{"primaryName":"Stage","primaryUnits":"ft","data":[{"validT'
        assert parser.parse(truncated) == 0

    def test_nan_inf_values_rejected(self, session):
        """JSON allows NaN/Infinity via Python's parser; observation guard
        must reject them rather than storing into the DB."""
        src = _make_source(session)
        parser = NWPSParser(url=NWPS_URL, session=session, source_id=src.id)
        # Note: stdlib json.loads accepts NaN/Infinity by default
        bad = (
            '{"primaryName":"Stage","primaryUnits":"ft",'
            '"secondaryName":"Flow","secondaryUnits":"cfs",'
            '"data":[{"validTime":"' + _recent(1) + '",'
            '"primary":NaN,"secondary":Infinity}]}'
        )
        parser.parse(bad)
        obs = session.query(Observation).filter_by(source_id=src.id).all()
        assert len(obs) == 0
