"""Tests for gauge lookups and the delete_gauge safety chokepoint.

Key invariant: a gauge that happens to have no linked reach is NOT a
deletion candidate. ``delete_gauge`` refuses to remove it unless the
caller has *separately* verified no active sources and no recent
observations, signalling intent with ``allow_with_sources=True``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kayak.db.gauges import GaugeDeletionGuardError, delete_gauge
from kayak.db.models import (
    DataType,
    Gauge,
    GaugeSource,
    Observation,
    Reach,
)


def _make_gauge_with_source(session, sample_source):
    """Gauge linked to `sample_source` through a GaugeSource row."""
    gauge = Gauge(name="lonely")
    session.add(gauge)
    session.flush()
    session.add(GaugeSource(gauge_id=gauge.id, source_id=sample_source.id))
    session.flush()
    return gauge


def test_delete_gauge_refuses_just_because_no_reaches(session, sample_source):
    """Reach-less is never sufficient reason to delete a gauge."""
    gauge = _make_gauge_with_source(session, sample_source)
    session.add(
        Observation(
            source_id=sample_source.id,
            data_type=DataType.flow,
            observed_at=datetime.now(UTC) - timedelta(hours=1),
            value=100.0,
        )
    )
    session.flush()

    # No reach links this gauge, but the gauge has a live source and
    # recent observations — deletion must be refused.
    with pytest.raises(GaugeDeletionGuardError):
        delete_gauge(session, gauge.id)

    assert session.get(Gauge, gauge.id) is not None


def test_reach_delete_does_not_delete_gauge(session, sample_gauge):
    """ON DELETE SET NULL on reach.gauge_id — deleting a reach leaves the gauge."""
    reach = Reach(
        name="r",
        display_name="R",
        sort_name="R",
        gauge_id=sample_gauge.id,
    )
    session.add(reach)
    session.flush()

    session.delete(reach)
    session.flush()

    assert session.get(Gauge, sample_gauge.id) is not None


def test_delete_gauge_ok_when_no_sources(session):
    """A gauge with zero sources and zero observations is deletable."""
    gauge = Gauge(name="retired")
    session.add(gauge)
    session.flush()

    delete_gauge(session, gauge.id)
    session.flush()

    assert session.get(Gauge, gauge.id) is None


def test_delete_gauge_ok_with_stale_opt_in(session, sample_source):
    """Explicit opt-in + no recent observations allows deletion."""
    gauge = _make_gauge_with_source(session, sample_source)
    # One observation, but older than min_stale_days=90.
    session.add(
        Observation(
            source_id=sample_source.id,
            data_type=DataType.flow,
            observed_at=datetime.now(UTC) - timedelta(days=200),
            value=100.0,
        )
    )
    session.flush()

    delete_gauge(session, gauge.id, allow_with_sources=True, min_stale_days=90)
    session.flush()

    assert session.get(Gauge, gauge.id) is None


def test_delete_gauge_blocks_recent_obs_even_with_opt_in(session, sample_source):
    """Opt-in alone isn't enough — min_stale_days still blocks recent data."""
    gauge = _make_gauge_with_source(session, sample_source)
    session.add(
        Observation(
            source_id=sample_source.id,
            data_type=DataType.flow,
            observed_at=datetime.now(UTC) - timedelta(days=30),
            value=100.0,
        )
    )
    session.flush()

    with pytest.raises(GaugeDeletionGuardError):
        delete_gauge(session, gauge.id, allow_with_sources=True, min_stale_days=90)

    assert session.get(Gauge, gauge.id) is not None


def test_delete_gauge_missing_raises(session):
    """Unknown gauge_id raises rather than silently no-op'ing."""
    with pytest.raises(GaugeDeletionGuardError):
        delete_gauge(session, 99999)
