"""Tests for the calc-rating CLI command."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from kayak.cli.calc_rating import calc_rating
from kayak.db.cache import get_latest
from kayak.db.models import (
    DataType,
    Gauge,
    GaugeSource,
    Rating,
    RatingData,
    Source,
)
from kayak.db.observations import get_observations, store_observation


def _noop(*a, **kw):
    pass


def _make_gauge_with_rating(session):
    """Create a gauge with a rating table and linked source. Returns IDs."""
    rating = Rating(url="https://example.com/rating", parser="usgs")
    session.add(rating)
    session.flush()

    for ft, cfs in [(0.0, 0.0), (5.0, 500.0), (10.0, 2000.0)]:
        session.add(RatingData(rating_id=rating.id, gauge_height_ft=ft, flow_cfs=cfs))

    gauge = Gauge(name="test_rating_gauge", rating_id=rating.id)
    session.add(gauge)
    session.flush()

    source = Source(name="test_rating_source", agency="USGS")
    session.add(source)
    session.flush()

    session.add(GaugeSource(gauge_id=gauge.id, source_id=source.id))
    session.flush()

    return gauge.id, source.id, rating.id


def _run_calc_rating(session):
    """Run calc_rating with session.close/commit patched to no-ops."""
    with (
        patch("kayak.cli.calc_rating.get_session", return_value=session),
        patch.object(session, "close", _noop),
        patch.object(session, "commit", _noop),
    ):
        calc_rating(SimpleNamespace())


def test_gauge_to_flow_conversion(session):
    """When only gauge data exists, calc_rating converts to flow."""
    _, source_id, _ = _make_gauge_with_rating(session)

    now = datetime.now(UTC)
    store_observation(session, source_id, DataType.gauge, now - timedelta(hours=2), 5.0)
    store_observation(session, source_id, DataType.gauge, now - timedelta(hours=1), 7.5)
    session.flush()

    _run_calc_rating(session)

    flow_obs = get_observations(session, source_id, DataType.flow)
    assert len(flow_obs) == 2
    values = sorted(o.value for o in flow_obs)
    assert values[0] == 500.0
    assert 1200 < values[1] < 1300


def test_flow_to_gauge_conversion(session):
    """When only flow data exists, calc_rating converts to gauge."""
    _, source_id, _ = _make_gauge_with_rating(session)

    now = datetime.now(UTC)
    store_observation(session, source_id, DataType.flow, now - timedelta(hours=1), 500.0)
    session.flush()

    _run_calc_rating(session)

    gauge_obs = get_observations(session, source_id, DataType.gauge)
    assert len(gauge_obs) == 1
    assert gauge_obs[0].value == 5.0


def test_fill_gaps_when_both_exist(session):
    """When both exist, calc_rating fills in missing timestamps."""
    _, source_id, _ = _make_gauge_with_rating(session)

    now = datetime.now(UTC)
    t1 = now - timedelta(hours=3)
    t2 = now - timedelta(hours=2)

    store_observation(session, source_id, DataType.gauge, t1, 5.0)
    store_observation(session, source_id, DataType.flow, t2, 500.0)
    session.flush()

    _run_calc_rating(session)

    flow_obs = get_observations(session, source_id, DataType.flow)
    gauge_obs = get_observations(session, source_id, DataType.gauge)
    assert len(flow_obs) == 2
    assert len(gauge_obs) == 2


def test_skip_empty_rating_table(session):
    """Gauges with empty rating tables are skipped."""
    rating = Rating(url="https://example.com/empty")
    session.add(rating)
    session.flush()

    gauge = Gauge(name="empty_rating_gauge", rating_id=rating.id)
    session.add(gauge)
    session.flush()

    source = Source(name="empty_rating_source", agency="USGS")
    session.add(source)
    session.flush()

    source_id = source.id
    session.add(GaugeSource(gauge_id=gauge.id, source_id=source_id))

    now = datetime.now(UTC)
    store_observation(session, source_id, DataType.gauge, now - timedelta(hours=1), 5.0)
    session.flush()

    _run_calc_rating(session)

    flow_obs = get_observations(session, source_id, DataType.flow)
    assert len(flow_obs) == 0


def test_latest_observation_updated(session):
    """calc_rating updates latest_observation after conversion."""
    _, source_id, _ = _make_gauge_with_rating(session)

    now = datetime.now(UTC)
    store_observation(session, source_id, DataType.gauge, now - timedelta(hours=1), 5.0)
    session.flush()

    _run_calc_rating(session)

    latest = get_latest(session, source_id, DataType.flow)
    assert latest is not None
    assert latest.value == 500.0
