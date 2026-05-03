"""Tests for the merge CLI command."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from kayak.cli.merge import merge
from kayak.db.cache import get_latest
from kayak.db.models import DataType, Gauge, GaugeSource, Source
from kayak.db.observations import get_observations, store_observation


def _noop(*a, **kw):
    pass


def _make_gauge_with_sources(session, num_sources=2):
    """Create a gauge linked to multiple sources. Returns (gauge_id, [source_ids])."""
    gauge = Gauge(name="merge_gauge")
    session.add(gauge)
    session.flush()

    source_ids = []
    for i in range(num_sources):
        src = Source(name=f"merge_source_{i}", agency="TEST")
        session.add(src)
        session.flush()
        session.add(GaugeSource(gauge_id=gauge.id, source_id=src.id))
        source_ids.append(src.id)
    session.flush()
    return gauge.id, source_ids


def _run_merge(session):
    """Run merge with session.close/commit patched to no-ops."""
    with (
        patch("kayak.cli.merge.get_session", return_value=session),
        patch.object(session, "close", _noop),
        patch.object(session, "commit", _noop),
    ):
        merge(SimpleNamespace())


def test_merge_two_sources_median(session):
    """Merging two sources uses median of overlapping timestamps."""
    _, source_ids = _make_gauge_with_sources(session, num_sources=3)
    target_id, src_a_id, src_b_id = source_ids

    now = datetime.now(UTC)
    t1 = now - timedelta(hours=2)

    store_observation(session, src_a_id, DataType.flow, t1, 100.0)
    store_observation(session, src_b_id, DataType.flow, t1, 200.0)
    session.flush()

    _run_merge(session)

    target_obs = get_observations(session, target_id, DataType.flow)
    assert len(target_obs) == 1
    assert target_obs[0].value == 150.0


def test_skip_single_source_gauge(session):
    """Gauges with only one source are skipped (no merge needed)."""
    _, source_ids = _make_gauge_with_sources(session, num_sources=1)
    src_id = source_ids[0]

    now = datetime.now(UTC)
    store_observation(session, src_id, DataType.flow, now - timedelta(hours=1), 100.0)
    session.flush()

    _run_merge(session)

    obs = get_observations(session, src_id, DataType.flow)
    assert len(obs) == 1


def test_latest_updated_after_merge(session):
    """Merge updates latest_observation for the target source."""
    _, source_ids = _make_gauge_with_sources(session, num_sources=3)
    target_id, src_a_id, src_b_id = source_ids

    now = datetime.now(UTC)
    t1 = now - timedelta(hours=1)
    store_observation(session, src_a_id, DataType.flow, t1, 300.0)
    store_observation(session, src_b_id, DataType.flow, t1, 500.0)
    session.flush()

    _run_merge(session)

    latest = get_latest(session, target_id, DataType.flow)
    assert latest is not None
    assert latest.value == 400.0


def test_multiple_data_types_independent(session):
    """Different data types are merged independently."""
    _, source_ids = _make_gauge_with_sources(session, num_sources=3)
    target_id, src_a_id, src_b_id = source_ids

    now = datetime.now(UTC)
    t1 = now - timedelta(hours=1)

    store_observation(session, src_a_id, DataType.flow, t1, 100.0)
    store_observation(session, src_b_id, DataType.flow, t1, 200.0)
    store_observation(session, src_a_id, DataType.gauge, t1, 5.0)
    store_observation(session, src_b_id, DataType.gauge, t1, 7.0)
    session.flush()

    _run_merge(session)

    flow_obs = get_observations(session, target_id, DataType.flow)
    gauge_obs = get_observations(session, target_id, DataType.gauge)
    assert len(flow_obs) == 1
    assert flow_obs[0].value == 150.0
    assert len(gauge_obs) == 1
    assert gauge_obs[0].value == 6.0
