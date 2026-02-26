"""Tests for data_db measurement storage."""

from datetime import datetime, timedelta, timezone

from kayak.db.data_db import (
    get_latest,
    get_measurements,
    get_rating_table,
    merge_stations,
    put_rating_table,
    store_measurement,
    update_latest,
)
from kayak.db.models import DataType, Measurement


def test_store_and_retrieve(session):
    now = datetime.now(timezone.utc)
    assert store_measurement(session, "s1", DataType.FLOW, now, 1500.0)
    session.flush()

    rows = get_measurements(session, "s1", DataType.FLOW)
    assert len(rows) == 1
    assert rows[0].value == 1500.0


def test_reject_future_timestamp(session):
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    assert not store_measurement(session, "s1", DataType.FLOW, future, 100.0)


def test_reject_negative_flow(session):
    now = datetime.now(timezone.utc)
    assert not store_measurement(session, "s1", DataType.FLOW, now, -10.0)


def test_negative_gage_ok(session):
    """Negative gage values should be accepted (unlike flow)."""
    now = datetime.now(timezone.utc)
    assert store_measurement(session, "s1", DataType.GAGE, now, -0.5)


def test_update_latest(session):
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=2)

    store_measurement(session, "s1", DataType.FLOW, old, 100.0)
    store_measurement(session, "s1", DataType.FLOW, now, 200.0)
    session.flush()

    update_latest(session, "s1", DataType.FLOW)
    session.flush()

    latest = get_latest(session, "s1", DataType.FLOW)
    assert latest is not None
    assert latest.value == 200.0
    assert latest.prev_value == 100.0
    assert latest.delta is not None
    assert latest.delta > 0  # Rising


def test_rating_table(session):
    entries = [(1.0, 100.0), (2.0, 400.0), (3.0, 900.0)]
    put_rating_table(session, "test_river", entries)
    session.flush()

    table = get_rating_table(session, "test_river")
    assert len(table) == 3
    assert table[0] == (1.0, 100.0)
    assert table[2] == (3.0, 900.0)


def test_merge_stations(session):
    now = datetime.now(timezone.utc)
    store_measurement(session, "src1", DataType.FLOW, now, 100.0)
    store_measurement(
        session, "src2", DataType.FLOW, now - timedelta(hours=1), 200.0
    )
    session.flush()

    count = merge_stations(
        session, "merged", ["src1", "src2"], DataType.FLOW,
        since=now - timedelta(days=1),
    )
    session.flush()

    merged = get_measurements(
        session, "merged", DataType.FLOW,
        since=now - timedelta(days=1),
    )
    assert len(merged) == 2
