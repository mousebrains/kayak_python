"""Tests for observation storage and per-source/gauge cache helpers."""

import math
from datetime import UTC, datetime, timedelta

from kayak.db.cache import get_all_latest, get_latest, update_latest, update_latest_gauge
from kayak.db.gauges import get_gauge_by_name
from kayak.db.models import DataType, FetchUrl, Gauge, GaugeSource, Rating, Source
from kayak.db.observations import (
    get_bulk_observations,
    get_observations,
    get_rating_table,
    put_rating_table,
    store_observation,
    store_observations,
)
from kayak.db.sources import get_negative_flow_source_ids, get_source_by_name


def _make_source(session, name="src1"):
    """Helper to create a Source with FetchUrl."""
    fu = FetchUrl(url=f"https://example.com/{name}", parser="nwps", is_active=True)
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


def test_reject_nan_value(session):
    src = _make_source(session)
    now = datetime.now(UTC)
    assert not store_observation(session, src.id, DataType.flow, now, math.nan)


def test_reject_pos_inf_value(session):
    src = _make_source(session)
    now = datetime.now(UTC)
    assert not store_observation(session, src.id, DataType.flow, now, math.inf)


def test_reject_neg_inf_value(session):
    src = _make_source(session)
    now = datetime.now(UTC)
    assert not store_observation(session, src.id, DataType.flow, now, -math.inf)


def test_negative_gauge_ok(session):
    """Negative gauge values should be accepted (unlike flow)."""
    src = _make_source(session)
    now = datetime.now(UTC)
    assert store_observation(session, src.id, DataType.gauge, now, -0.5)


def test_update_latest(session):
    src = _make_source(session)
    now = datetime.now(UTC)
    old = now - timedelta(hours=7)

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


# ---------------------------------------------------------------------------
# Batch store_observations
# ---------------------------------------------------------------------------


def test_store_observations_batch(session):
    """Multiple rows stored in one call."""
    src = _make_source(session)
    now = datetime.now(UTC)
    rows = [
        {
            "source_id": src.id,
            "data_type": DataType.flow,
            "observed_at": now - timedelta(hours=i),
            "value": 100.0 + i,
        }
        for i in range(5)
    ]
    count = store_observations(session, rows)
    session.flush()
    assert count == 5
    obs = get_observations(session, src.id, DataType.flow)
    assert len(obs) == 5


def test_store_observations_upsert(session):
    """Batch with conflicts updates existing rows."""
    src = _make_source(session)
    now = datetime.now(UTC)
    store_observation(session, src.id, DataType.flow, now, 100.0)
    session.flush()

    rows = [
        {"source_id": src.id, "data_type": DataType.flow, "observed_at": now, "value": 200.0},
        {
            "source_id": src.id,
            "data_type": DataType.flow,
            "observed_at": now - timedelta(hours=1),
            "value": 300.0,
        },
    ]
    count = store_observations(session, rows)
    session.flush()
    assert count == 2

    obs = get_observations(session, src.id, DataType.flow)
    assert len(obs) == 2
    # Most recent should be the updated value
    assert obs[0].value == 200.0


def test_store_observations_validation(session):
    """Invalid rows rejected, valid rows stored."""
    src = _make_source(session)
    now = datetime.now(UTC)
    future = now + timedelta(hours=2)
    rows = [
        {"source_id": src.id, "data_type": DataType.flow, "observed_at": now, "value": 100.0},
        {
            "source_id": src.id,
            "data_type": DataType.flow,
            "observed_at": future,
            "value": 200.0,
        },  # future
        {
            "source_id": src.id,
            "data_type": DataType.flow,
            "observed_at": now - timedelta(hours=1),
            "value": -10.0,
        },  # negative flow
    ]
    count = store_observations(session, rows)
    session.flush()
    assert count == 1

    obs = get_observations(session, src.id, DataType.flow)
    assert len(obs) == 1
    assert obs[0].value == 100.0


def test_store_observations_empty(session):
    """Empty list returns 0."""
    assert store_observations(session, []) == 0


# ---------------------------------------------------------------------------
# Bulk query functions
# ---------------------------------------------------------------------------


def test_get_all_latest(session):
    """get_all_latest returns dict keyed by (source_id, data_type)."""
    src1 = _make_source(session, "lat1")
    src2 = _make_source(session, "lat2")
    now = datetime.now(UTC)

    store_observation(session, src1.id, DataType.flow, now, 100.0)
    store_observation(session, src2.id, DataType.gauge, now, 5.0)
    session.flush()
    update_latest(session, src1.id, DataType.flow)
    update_latest(session, src2.id, DataType.gauge)
    session.flush()

    result = get_all_latest(session, [src1.id, src2.id])
    assert (src1.id, DataType.flow) in result
    assert result[(src1.id, DataType.flow)].value == 100.0
    assert (src2.id, DataType.gauge) in result
    assert result[(src2.id, DataType.gauge)].value == 5.0


def test_get_all_latest_empty(session):
    """get_all_latest with empty list returns empty dict."""
    assert get_all_latest(session, []) == {}


def test_get_bulk_observations(session):
    """get_bulk_observations groups by source_id."""
    src1 = _make_source(session, "bulk1")
    src2 = _make_source(session, "bulk2")
    now = datetime.now(UTC)

    for i in range(3):
        store_observation(session, src1.id, DataType.flow, now - timedelta(hours=i), 100.0 + i)
        store_observation(session, src2.id, DataType.flow, now - timedelta(hours=i), 200.0 + i)
    session.flush()

    since = now - timedelta(hours=5)
    result = get_bulk_observations(session, [src1.id, src2.id], DataType.flow, since)
    assert src1.id in result
    assert src2.id in result
    assert len(result[src1.id]) == 3
    assert len(result[src2.id]) == 3


# ---------------------------------------------------------------------------
# get_source_by_name / get_gauge_by_name
# ---------------------------------------------------------------------------


def test_get_source_by_name_found(session):
    src = _make_source(session, name="findme")
    result = get_source_by_name(session, "findme")
    assert result is not None
    assert result.id == src.id


def test_get_source_by_name_not_found(session):
    assert get_source_by_name(session, "nonexistent") is None


def test_get_source_by_name_duplicates(session):
    src1 = Source(name="NMFO3", agency="NWS")
    src2 = Source(name="NMFO3", agency="nwps")
    session.add_all([src1, src2])
    session.flush()
    result = get_source_by_name(session, "NMFO3")
    assert result is not None
    assert result.id in {src1.id, src2.id}


def test_get_gauge_by_name_found(session):
    gauge = Gauge(name="test_gauge")
    session.add(gauge)
    session.flush()
    result = get_gauge_by_name(session, "test_gauge")
    assert result is not None
    assert result.id == gauge.id


def test_get_gauge_by_name_not_found(session):
    assert get_gauge_by_name(session, "nonexistent") is None


# ---------------------------------------------------------------------------
# update_latest_gauge
# ---------------------------------------------------------------------------


def test_update_latest_gauge_basic(session):
    src = _make_source(session, name="gauge_src")
    gauge = Gauge(name="test_g")
    session.add(gauge)
    session.flush()
    gs = GaugeSource(gauge_id=gauge.id, source_id=src.id)
    session.add(gs)
    session.flush()

    now = datetime.now(UTC)
    store_observation(session, src.id, DataType.flow, now - timedelta(hours=12), 100.0)
    store_observation(session, src.id, DataType.flow, now, 150.0)
    session.flush()

    update_latest_gauge(session, gauge.id, DataType.flow)
    session.flush()

    from kayak.db.models import LatestGaugeObservation

    latest = (
        session.query(LatestGaugeObservation)
        .filter_by(gauge_id=gauge.id, data_type=DataType.flow)
        .one_or_none()
    )
    assert latest is not None
    assert latest.value == 150.0


# ---------------------------------------------------------------------------
# allow_negative_flow flag
# ---------------------------------------------------------------------------


def _make_tidal_gauge_source(session):
    """Create a gauge with allow_negative_flow=True and a linked source."""
    src = _make_source(session, "tidal_src")
    gauge = Gauge(name="tidal_gauge", allow_negative_flow=True)
    session.add(gauge)
    session.flush()
    gs = GaugeSource(gauge_id=gauge.id, source_id=src.id)
    session.add(gs)
    session.flush()
    return src, gauge


def test_get_negative_flow_source_ids_empty(session):
    """No gauges with the flag returns empty set."""
    assert get_negative_flow_source_ids(session) == set()


def test_get_negative_flow_source_ids(session):
    """Sources linked to allow_negative_flow gauges are returned."""
    src, _gauge = _make_tidal_gauge_source(session)
    ids = get_negative_flow_source_ids(session)
    assert src.id in ids


def test_store_observation_negative_flow_allowed(session):
    """Negative flow accepted when source is in allow set."""
    src, _gauge = _make_tidal_gauge_source(session)
    now = datetime.now(UTC)
    allowed = get_negative_flow_source_ids(session)
    assert store_observation(
        session,
        src.id,
        DataType.flow,
        now,
        -100.0,
        allow_negative_flow_sources=allowed,
    )
    session.flush()
    rows = get_observations(session, src.id, DataType.flow)
    assert len(rows) == 1
    assert rows[0].value == -100.0


def test_store_observation_negative_flow_rejected_by_default(session):
    """Negative flow still rejected for sources not in allow set."""
    src, _gauge = _make_tidal_gauge_source(session)
    now = datetime.now(UTC)
    # Pass empty set — source not allowed
    assert not store_observation(
        session,
        src.id,
        DataType.flow,
        now,
        -100.0,
        allow_negative_flow_sources=set(),
    )


def test_store_observations_batch_negative_flow_allowed(session):
    """Batch store accepts negative flow for allowed sources."""
    src, _gauge = _make_tidal_gauge_source(session)
    now = datetime.now(UTC)
    allowed = get_negative_flow_source_ids(session)
    rows = [
        {"source_id": src.id, "data_type": DataType.flow, "observed_at": now, "value": -50.0},
        {
            "source_id": src.id,
            "data_type": DataType.flow,
            "observed_at": now - timedelta(hours=1),
            "value": -200.0,
        },
    ]
    count = store_observations(session, rows, allow_negative_flow_sources=allowed)
    session.flush()
    assert count == 2


def test_store_observations_batch_negative_flow_rejected(session):
    """Batch store rejects negative flow when source not in allow set."""
    src = _make_source(session, "normal_src")
    now = datetime.now(UTC)
    rows = [
        {"source_id": src.id, "data_type": DataType.flow, "observed_at": now, "value": -50.0},
    ]
    count = store_observations(session, rows, allow_negative_flow_sources=set())
    assert count == 0
