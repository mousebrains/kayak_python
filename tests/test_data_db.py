"""Tests for data_db observation storage."""

from datetime import UTC, datetime, timedelta

from kayak.db.data_db import (
    get_latest,
    get_observations,
    get_rating_table,
    merge_sources,
    put_rating_table,
    store_observation,
    update_latest,
)
from kayak.db.models import DataType, FetchUrl, Rating, Source


def _make_source(session, name="src1"):
    """Helper to create a Source with FetchUrl."""
    fu = FetchUrl(url=f"https://example.com/{name}", parser="usgs", is_active=True)
    session.add(fu)
    session.flush()
    src = Source(name=name, fetch_url_id=fu.id)
    session.add(src)
    session.flush()
    return src


def test_store_and_retrieve(session):
    src = _make_source(session)
    now = datetime.now(UTC)
    assert store_observation(session, src.id, DataType.flow, now, 1500.0)
    session.flush()

    rows = get_observations(session, src.id, DataType.flow)
    assert len(rows) == 1
    assert rows[0].value == 1500.0


def test_reject_future_timestamp(session):
    src = _make_source(session)
    future = datetime.now(UTC) + timedelta(hours=2)
    assert not store_observation(session, src.id, DataType.flow, future, 100.0)


def test_reject_negative_flow(session):
    src = _make_source(session)
    now = datetime.now(UTC)
    assert not store_observation(session, src.id, DataType.flow, now, -10.0)


def test_negative_gauge_ok(session):
    """Negative gauge values should be accepted (unlike flow)."""
    src = _make_source(session)
    now = datetime.now(UTC)
    assert store_observation(session, src.id, DataType.gauge, now, -0.5)


def test_update_latest(session):
    src = _make_source(session)
    now = datetime.now(UTC)
    old = now - timedelta(hours=2)

    store_observation(session, src.id, DataType.flow, old, 100.0)
    store_observation(session, src.id, DataType.flow, now, 200.0)
    session.flush()

    update_latest(session, src.id, DataType.flow)
    session.flush()

    latest = get_latest(session, src.id, DataType.flow)
    assert latest is not None
    assert latest.value == 200.0
    assert latest.prev_value == 100.0
    assert latest.delta_per_hour is not None
    assert latest.delta_per_hour > 0  # Rising


def test_rating_table(session):
    rating = Rating(url="https://example.com/rating")
    session.add(rating)
    session.flush()

    entries = [(1.0, 100.0), (2.0, 400.0), (3.0, 900.0)]
    put_rating_table(session, rating.id, entries)
    session.flush()

    table = get_rating_table(session, rating.id)
    assert len(table) == 3
    assert table[0] == (1.0, 100.0)
    assert table[2] == (3.0, 900.0)


def test_merge_sources(session):
    src1 = _make_source(session, "src1")
    src2 = _make_source(session, "src2")
    merged = _make_source(session, "merged")

    now = datetime.now(UTC)
    store_observation(session, src1.id, DataType.flow, now, 100.0)
    store_observation(
        session, src2.id, DataType.flow, now - timedelta(hours=1), 200.0
    )
    session.flush()

    merge_sources(
        session, merged.id, [src1.id, src2.id], DataType.flow,
        since=now - timedelta(days=1),
    )
    session.flush()

    merged_obs = get_observations(
        session, merged.id, DataType.flow,
        since=now - timedelta(days=1),
    )
    assert len(merged_obs) == 2


def test_store_observation_string_type(session):
    """DataType can be passed as string."""
    src = _make_source(session)
    now = datetime.now(UTC)
    assert store_observation(session, src.id, "flow", now, 500.0)
    session.flush()

    rows = get_observations(session, src.id, DataType.flow)
    assert len(rows) == 1


def test_store_observation_upsert(session):
    """Duplicate source_id/observed_at/data_type should update value."""
    src = _make_source(session)
    now = datetime.now(UTC)
    store_observation(session, src.id, DataType.flow, now, 100.0)
    session.flush()

    store_observation(session, src.id, DataType.flow, now, 200.0)
    session.flush()

    rows = get_observations(session, src.id, DataType.flow)
    assert len(rows) == 1
    assert rows[0].value == 200.0
