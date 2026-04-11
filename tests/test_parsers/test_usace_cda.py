"""Tests for the USACE CDA (Corps Data Access) parser."""

import json

from kayak.db.models import DataType, FetchUrl, Observation, Source
from kayak.parsers.usace_cda import USACECDAParser

CDA_BASIC = json.dumps(
    {
        "GPR": {
            "name": "Green Peter Reservoir",
            "timeseries": {
                "GPR.Flow-Out.Inst.0.0.Best": {
                    "parameter": "Flow-Out",
                    "units": "cfs",
                    "values": [
                        ["2024-06-15T12:00:00", 50.0, 0],
                        ["2024-06-15T13:00:00", 55.0, 0],
                    ],
                }
            },
        }
    }
)

CDA_MULTI_PARAM = json.dumps(
    {
        "HCR": {
            "name": "Hills Creek Dam",
            "timeseries": {
                "HCR.Flow-Out.Inst.0.0.Best": {
                    "parameter": "Flow-Out",
                    "units": "cfs",
                    "values": [
                        ["2024-06-15T12:00:00", 483.97, 0],
                    ],
                },
                "HCR.Elev-Forebay.Inst.0.0.Best": {
                    "parameter": "Elev-Forebay",
                    "units": "ft",
                    "values": [
                        ["2024-06-15T12:00:00", 1480.5, 0],
                    ],
                },
            },
        }
    }
)

CDA_FUTURE = json.dumps(
    {
        "GPR": {
            "timeseries": {
                "GPR.Flow-Out.Inst.0.0.Best": {
                    "parameter": "Flow-Out",
                    "units": "cfs",
                    "values": [
                        ["2099-01-01T00:00:00", 100.0, 0],
                    ],
                }
            },
        }
    }
)

CDA_EMPTY_VALUES = json.dumps(
    {
        "GPR": {
            "timeseries": {
                "GPR.Flow-Out.Inst.0.0.Best": {
                    "parameter": "Flow-Out",
                    "units": "cfs",
                    "values": [],
                }
            },
        }
    }
)

CDA_NULL_VALUE = json.dumps(
    {
        "GPR": {
            "timeseries": {
                "GPR.Flow-Out.Inst.0.0.Best": {
                    "parameter": "Flow-Out",
                    "units": "cfs",
                    "values": [
                        ["2024-06-15T12:00:00", None, 0],
                    ],
                }
            },
        }
    }
)

CDA_URL = (
    "https://www.nwd-wc.usace.army.mil/dd/common/web_service/webexec/getjson"
    "?query=%5B%22GPR.Flow-Out.Inst.0.0.Best%22%5D&timezone=GMT&backward=2d&forward=0d"
)


def _make_source(session, name="GPR"):
    fu = FetchUrl(url=f"https://example.com/{name}", parser="usace.cda", is_active=True)
    session.add(fu)
    session.flush()
    src = Source(name=name, fetch_url_id=fu.id)
    session.add(src)
    session.flush()
    return src


class TestUSACECDABasic:
    def test_parse_outflow(self, session):
        """Parse JSON with outflow time series."""
        src = _make_source(session, "GPR")
        parser = USACECDAParser(url=CDA_URL, session=session, source_id=src.id)
        count = parser.parse(CDA_BASIC)

        assert count == 2

        flows = (
            session.query(Observation)
            .filter_by(source_id=src.id, data_type=DataType.flow)
            .order_by(Observation.observed_at)
            .all()
        )
        assert len(flows) == 2
        assert flows[0].value == 50.0
        assert flows[1].value == 55.0

    def test_parse_multi_parameter(self, session):
        """Parse JSON with both outflow and forebay elevation."""
        src = _make_source(session, "HCR")
        parser = USACECDAParser(url=CDA_URL, session=session, source_id=src.id)
        count = parser.parse(CDA_MULTI_PARAM)

        assert count == 2

        flows = (
            session.query(Observation).filter_by(source_id=src.id, data_type=DataType.flow).all()
        )
        assert len(flows) == 1
        assert flows[0].value == 483.97

        gauges = (
            session.query(Observation).filter_by(source_id=src.id, data_type=DataType.gauge).all()
        )
        assert len(gauges) == 1
        assert gauges[0].value == 1480.5


class TestUSACECDAEdgeCases:
    def test_future_timestamps_rejected(self, session):
        """Timestamps in the future should be skipped."""
        src = _make_source(session)
        parser = USACECDAParser(url=CDA_URL, session=session, source_id=src.id)
        count = parser.parse(CDA_FUTURE)
        assert count == 0

    def test_empty_values(self, session):
        """Empty values array should return 0."""
        src = _make_source(session)
        parser = USACECDAParser(url=CDA_URL, session=session, source_id=src.id)
        count = parser.parse(CDA_EMPTY_VALUES)
        assert count == 0

    def test_null_value_skipped(self, session):
        """Null values should be skipped."""
        src = _make_source(session)
        parser = USACECDAParser(url=CDA_URL, session=session, source_id=src.id)
        count = parser.parse(CDA_NULL_VALUE)
        assert count == 0

    def test_invalid_json(self, session):
        """Non-JSON input should return 0."""
        src = _make_source(session)
        parser = USACECDAParser(url=CDA_URL, session=session, source_id=src.id)
        assert parser.parse("") == 0
        assert parser.parse("not json") == 0

    def test_dry_run(self, session):
        """Dry run should count but not store observations."""
        src = _make_source(session)
        parser = USACECDAParser(url=CDA_URL, session=session, source_id=src.id, dry_run=True)
        count = parser.parse(CDA_BASIC)
        assert count == 2
        assert session.query(Observation).count() == 0
