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
    rating = Rating(url="https://example.com/rating", parser="nwps")
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


def test_both_exist_uses_pre_loop_time_sets(session):
    """Pre-Phase-3 pin: in the both-exist branch, newly-stored rows must
    NOT feed back into the in-loop time set.

    Set up: gauge at t1 only, flow at t2 only. After calc_rating both
    columns should each have exactly two rows, with identical timestamps
    — neither more (no recursive re-derivation: the gauge row written at
    t2 must not trigger a fresh flow derivation at t2) nor fewer (no
    skipped fill).

    The Phase 3 refactor collapses the 3-way if/elif/else into two parallel
    "fill missing" calls. The pre-loop snapshot of `{observed_at}` times is
    the invariant that keeps this safe. Regression: if the time-set were
    re-computed AFTER each fill call, the second call would observe the
    rows just written by the first.
    """
    _, source_id, _ = _make_gauge_with_rating(session)

    now = datetime.now(UTC)
    t1 = now - timedelta(hours=3)
    t2 = now - timedelta(hours=2)

    store_observation(session, source_id, DataType.gauge, t1, 5.0)
    store_observation(session, source_id, DataType.flow, t2, 500.0)
    session.flush()

    _run_calc_rating(session)

    gauge_times = {o.observed_at for o in get_observations(session, source_id, DataType.gauge)}
    flow_times = {o.observed_at for o in get_observations(session, source_id, DataType.flow)}

    # Exactly 2 rows per column — no re-derivation past the original two
    # input timestamps.
    assert len(gauge_times) == 2
    assert len(flow_times) == 2
    # Both columns end up with identical timestamps (each side cross-filled
    # the missing one). Microsecond storage differs between input and DB
    # round-trip, so compare the two stored sets to each other rather than
    # to the input t1/t2 directly.
    assert gauge_times == flow_times


def test_zero_flow_value_not_stored(session):
    """Pre-Phase-3 pin: the `val > 0` guard on flow output must not be
    dropped by the refactor.

    Set up: gauge at 0.0 ft, no flow rows. `interpolate_rating(feet_to_cfs,
    0.0)` returns 0.0 (table entry (0.0, 0.0)). The current calc_rating.py
    guards `val > 0` before storing a flow row (lines 96 and 129 — only on
    flow output, not gauge). Without that guard, a flow row at value=0.0
    would land and pollute the latest cache with a zero reading.

    The Phase 3 refactor collapses the only-gauge and both-exist branches
    into a single `_fill_flow_from_gauge` helper. That helper must preserve
    the `val > 0` filter; this test fails if it doesn't.
    """
    _, source_id, _ = _make_gauge_with_rating(session)

    now = datetime.now(UTC)
    store_observation(session, source_id, DataType.gauge, now - timedelta(hours=1), 0.0)
    session.flush()

    _run_calc_rating(session)

    flow_obs = get_observations(session, source_id, DataType.flow)
    assert len(flow_obs) == 0
