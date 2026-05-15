"""Session-free tests for NWPSParser.parse_records (T3.1).

Mirrors the example-based assertions in ``test_nwps.py`` but against
the pure ``parse_records`` entry point — no ``session`` fixture, no DB.
``parse_records`` returns a list of ``ObservationRecord`` dataclasses
that we can compare with ``==`` since the dataclass is frozen and
fields are hashable.

The legacy ``test_nwps.py`` continues to cover the DB-write path
(``dump_to_db`` → ``_flush_buffer``); this file adds coverage for the
pure-function contract that future code can call without bringing in
a SQLAlchemy session.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from kayak.db.models import DataType
from kayak.parsers.base import ObservationRecord
from kayak.parsers.nwps import NWPSParser

# Pin "now" so future-timestamp filtering is deterministic — same idea
# as the now= parameter on parse_records itself.
_PINNED_NOW = datetime(2026, 6, 15, 14, 0, 0, tzinfo=UTC)
_STATION_URL = "https://api.water.noaa.gov/nwps/v1/gauges/TESTLID/stageflow/observed"


def _new_parser() -> NWPSParser:
    """Construct an NWPSParser without a session — parse_records doesn't use it.

    The base ``__init__`` requires a session positionally, so pass a
    sentinel value. ``parse_records`` MUST NOT touch ``self.session``;
    if it does, a future regression would explode here rather than
    silently using a wrong session.
    """
    return NWPSParser(url=_STATION_URL, session=None)  # type: ignore[arg-type]


def test_parse_records_returns_stage_and_flow():
    """Happy path: two entries, each with stage + flow, returns 4 records."""
    payload = json.dumps(
        {
            "primaryUnits": "ft",
            "secondaryUnits": "cfs",
            "data": [
                {"validTime": "2026-06-15T12:00:00Z", "primary": 4.20, "secondary": 500.0},
                {"validTime": "2026-06-15T13:00:00Z", "primary": 4.30, "secondary": 525.0},
            ],
        }
    )
    parser = _new_parser()
    records = parser.parse_records(payload, now=_PINNED_NOW)
    assert records == [
        ObservationRecord(
            "TESTLID", DataType.gauge, datetime(2026, 6, 15, 12, 0, tzinfo=UTC), 4.20
        ),
        ObservationRecord(
            "TESTLID", DataType.flow, datetime(2026, 6, 15, 12, 0, tzinfo=UTC), 500.0
        ),
        ObservationRecord(
            "TESTLID", DataType.gauge, datetime(2026, 6, 15, 13, 0, tzinfo=UTC), 4.30
        ),
        ObservationRecord(
            "TESTLID", DataType.flow, datetime(2026, 6, 15, 13, 0, tzinfo=UTC), 525.0
        ),
    ]


def test_parse_records_kcfs_converts_to_cfs():
    """``secondaryUnits=kcfs`` multiplies by 1000."""
    payload = json.dumps(
        {
            "primaryUnits": "ft",
            "secondaryUnits": "kcfs",
            "data": [
                {"validTime": "2026-06-15T12:00:00Z", "primary": 4.0, "secondary": 1.5},
            ],
        }
    )
    parser = _new_parser()
    records = parser.parse_records(payload, now=_PINNED_NOW)
    # Stage record first, then flow (parse order). Flow is 1500 = 1.5 * 1000.
    assert [r.value for r in records] == [4.0, 1500.0]


def test_parse_records_filters_sentinels():
    """``-999`` / ``-9999`` in primary or secondary yields no record for that field."""
    payload = json.dumps(
        {
            "primaryUnits": "ft",
            "secondaryUnits": "cfs",
            "data": [
                {"validTime": "2026-06-15T12:00:00Z", "primary": -999, "secondary": 500.0},
                {"validTime": "2026-06-15T13:00:00Z", "primary": 4.0, "secondary": -9999},
            ],
        }
    )
    parser = _new_parser()
    records = parser.parse_records(payload, now=_PINNED_NOW)
    # First entry: stage sentinel → only flow record.
    # Second entry: flow sentinel → only stage record.
    assert [(r.data_type, r.value) for r in records] == [
        (DataType.flow, 500.0),
        (DataType.gauge, 4.0),
    ]


def test_parse_records_filters_future_timestamps():
    """Entries with ``validTime > now`` are dropped."""
    payload = json.dumps(
        {
            "primaryUnits": "ft",
            "secondaryUnits": "cfs",
            "data": [
                {"validTime": "2099-01-01T00:00:00Z", "primary": 4.0, "secondary": 500.0},
            ],
        }
    )
    parser = _new_parser()
    assert parser.parse_records(payload, now=_PINNED_NOW) == []


def test_parse_records_returns_empty_on_malformed_json():
    """Parse failure yields ``[]`` — caller decides how to handle it.

    The wrapper ``parse()`` is the one that emits the error log; the
    pure path stays silent so it's testable without log assertions.
    """
    parser = _new_parser()
    assert parser.parse_records("not json", now=_PINNED_NOW) == []
    assert parser.parse_records("", now=_PINNED_NOW) == []


def test_parse_records_negative_flow_dropped():
    """``flow < 0`` rows are dropped (non-sentinel negatives still unphysical)."""
    payload = json.dumps(
        {
            "primaryUnits": "ft",
            "secondaryUnits": "cfs",
            "data": [
                {"validTime": "2026-06-15T12:00:00Z", "primary": 4.0, "secondary": -50.0},
            ],
        }
    )
    parser = _new_parser()
    records = parser.parse_records(payload, now=_PINNED_NOW)
    # Stage survives, flow drops.
    assert [r.data_type for r in records] == [DataType.gauge]
