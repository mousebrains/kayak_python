"""Session-free tests for USACECDAParser.parse_records (T3.1)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from kayak.db.models import DataType
from kayak.parsers.base import ObservationRecord
from kayak.parsers.usace_cda import USACECDAParser

_PINNED_NOW = datetime(2026, 6, 15, 14, 0, 0, tzinfo=UTC)
_URL = "https://example.com/cda?timezone=GMT&backward=2d&forward=0d"


def _new_parser(url: str = _URL) -> USACECDAParser:
    """Construct without a session; parse_records doesn't use it."""
    return USACECDAParser(url=url, session=None)  # type: ignore[arg-type]


def test_parse_records_flow_out():
    """Recognized parameter (Flow-Out) yields a flow record per non-null value."""
    payload = json.dumps(
        {
            "GPR": {
                "name": "Green Peter Reservoir",
                "timeseries": {
                    "GPR.Flow-Out.Inst.0.0.Best": {
                        "parameter": "Flow-Out",
                        "units": "cfs",
                        "values": [
                            ["2026-06-15T12:00:00", 50.0, 0],
                            ["2026-06-15T13:00:00", 55.0, 0],
                        ],
                    }
                },
            }
        }
    )
    records = _new_parser().parse_records(payload, now=_PINNED_NOW)
    assert records == [
        ObservationRecord("GPR", DataType.flow, datetime(2026, 6, 15, 12, 0, tzinfo=UTC), 50.0),
        ObservationRecord("GPR", DataType.flow, datetime(2026, 6, 15, 13, 0, tzinfo=UTC), 55.0),
    ]


def test_parse_records_multi_parameter():
    """Flow-Out + Elev-Forebay both surface, distinct data types."""
    payload = json.dumps(
        {
            "HCR": {
                "timeseries": {
                    "HCR.Flow-Out.Inst.0.0.Best": {
                        "parameter": "Flow-Out",
                        "values": [["2026-06-15T12:00:00", 483.97, 0]],
                    },
                    "HCR.Elev-Forebay.Inst.0.0.Best": {
                        "parameter": "Elev-Forebay",
                        "values": [["2026-06-15T12:00:00", 1480.5, 0]],
                    },
                }
            }
        }
    )
    records = _new_parser().parse_records(payload, now=_PINNED_NOW)
    by_type = {r.data_type: r for r in records}
    assert by_type[DataType.flow].value == 483.97
    assert by_type[DataType.gauge].value == 1480.5


def test_parse_records_unknown_parameter_skipped():
    payload = json.dumps(
        {
            "X": {
                "timeseries": {
                    "X.Wind-Speed.Inst.0.0.Best": {
                        "parameter": "Wind-Speed",
                        "values": [["2026-06-15T12:00:00", 5.0, 0]],
                    }
                }
            }
        }
    )
    assert _new_parser().parse_records(payload, now=_PINNED_NOW) == []


def test_parse_records_null_value_skipped():
    payload = json.dumps(
        {
            "X": {
                "timeseries": {
                    "X.Flow-Out.Inst.0.0.Best": {
                        "parameter": "Flow-Out",
                        "values": [["2026-06-15T12:00:00", None, 0]],
                    }
                }
            }
        }
    )
    assert _new_parser().parse_records(payload, now=_PINNED_NOW) == []


def test_parse_records_future_timestamps_skipped():
    payload = json.dumps(
        {
            "X": {
                "timeseries": {
                    "X.Flow-Out.Inst.0.0.Best": {
                        "parameter": "Flow-Out",
                        "values": [["2099-01-01T00:00:00", 100.0, 0]],
                    }
                }
            }
        }
    )
    assert _new_parser().parse_records(payload, now=_PINNED_NOW) == []


def test_parse_records_url_without_gmt_raises():
    """The timezone=GMT contract is enforced before parsing — bad URL → ValueError."""
    parser = _new_parser(url="https://example.com/cda?timezone=PST&backward=2d&forward=0d")
    with pytest.raises(ValueError, match="timezone=GMT"):
        parser.parse_records('{"X": {}}', now=_PINNED_NOW)


def test_parse_records_returns_empty_on_malformed_json():
    """Parse failure yields ``[]``; the wrapper handles the logging."""
    parser = _new_parser()
    assert parser.parse_records("not json", now=_PINNED_NOW) == []
    assert parser.parse_records("", now=_PINNED_NOW) == []


def test_parse_records_kcfs_flow_scaled_to_cfs():
    """Lower-Columbia dams report Flow-Out in kcfs; scale to the canonical cfs."""
    payload = json.dumps(
        {
            "JDA": {
                "timeseries": {
                    "JDA.Flow-Out.Ave.1Hour.1Hour.CBT-REV": {
                        "parameter": "Flow-Out",
                        "units": "kcfs",
                        "values": [["2026-06-15T12:00:00", 182.9, 0]],
                    }
                }
            }
        }
    )
    records = _new_parser().parse_records(payload, now=_PINNED_NOW)
    assert records == [
        ObservationRecord("JDA", DataType.flow, datetime(2026, 6, 15, 12, 0, tzinfo=UTC), 182900.0),
    ]


def test_parse_records_cfs_flow_not_scaled():
    """The Willamette dams' .Inst.0.0.Best series is already cfs — left unscaled."""
    payload = json.dumps(
        {
            "GPR": {
                "timeseries": {
                    "GPR.Flow-Out.Inst.0.0.Best": {
                        "parameter": "Flow-Out",
                        "units": "cfs",
                        "values": [["2026-06-15T12:00:00", 4110.0, 0]],
                    }
                }
            }
        }
    )
    records = _new_parser().parse_records(payload, now=_PINNED_NOW)
    assert records == [
        ObservationRecord("GPR", DataType.flow, datetime(2026, 6, 15, 12, 0, tzinfo=UTC), 4110.0),
    ]


def test_parse_records_kcfs_units_tolerate_whitespace_and_case():
    """The unit match is strip()+lower(), so a stray ' KCFS ' still scales."""
    payload = json.dumps(
        {
            "BON": {
                "timeseries": {
                    "BON.Flow-Out.Ave.1Hour.1Hour.CBT-REV": {
                        "parameter": "Flow-Out",
                        "units": " KCFS ",
                        "values": [["2026-06-15T12:00:00", 194.9, 0]],
                    }
                }
            }
        }
    )
    records = _new_parser().parse_records(payload, now=_PINNED_NOW)
    assert records == [
        ObservationRecord("BON", DataType.flow, datetime(2026, 6, 15, 12, 0, tzinfo=UTC), 194900.0),
    ]
