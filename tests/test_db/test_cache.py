"""Cache pruning: latest_* rows must disappear when backing observations vanish.

Regression for the stale-cache drift observed in the live DB (2026-04-23):
`update_latest` / `update_latest_gauge` returned early when the source had no
observations, leaving orphaned rows that outlived the raw series that produced
them (e.g. after `decimate` purged everything older than retention).
"""

from __future__ import annotations

from datetime import UTC, datetime

from kayak.db import cache
from kayak.db.models import (
    DataType,
    LatestGaugeObservation,
    LatestObservation,
    Observation,
)


def _add_obs(session, source_id: int, data_type: DataType, value: float) -> None:
    session.add(
        Observation(
            source_id=source_id,
            data_type=data_type,
            observed_at=datetime(2026, 4, 23, 12, 0, tzinfo=UTC),
            value=value,
        )
    )
    session.flush()


def test_update_latest_prunes_stale_row_when_observations_gone(session, sample_source):
    _add_obs(session, sample_source.id, DataType.flow, 100.0)
    cache.update_latest(session, sample_source.id, DataType.flow)
    assert cache.get_latest(session, sample_source.id, DataType.flow) is not None

    session.query(Observation).filter_by(source_id=sample_source.id).delete()
    session.flush()

    cache.update_latest(session, sample_source.id, DataType.flow)
    assert cache.get_latest(session, sample_source.id, DataType.flow) is None


def test_update_latest_noop_when_no_cache_and_no_observations(session, sample_source):
    cache.update_latest(session, sample_source.id, DataType.flow)
    assert (
        session.query(LatestObservation)
        .filter_by(source_id=sample_source.id, data_type=DataType.flow)
        .count()
        == 0
    )


def test_update_latest_gauge_prunes_stale_row_when_observations_gone(
    session, linked_source_gauge
):
    source, gauge = linked_source_gauge
    _add_obs(session, source.id, DataType.flow, 250.0)
    cache.update_latest_gauge(session, gauge.id, DataType.flow)
    assert cache.get_latest_gauge(session, gauge.id, DataType.flow) is not None

    session.query(Observation).filter_by(source_id=source.id).delete()
    session.flush()

    cache.update_latest_gauge(session, gauge.id, DataType.flow)
    assert cache.get_latest_gauge(session, gauge.id, DataType.flow) is None


def test_update_latest_gauge_prunes_stale_row_when_sources_unlinked(
    session, linked_source_gauge
):
    from kayak.db.models import GaugeSource

    source, gauge = linked_source_gauge
    _add_obs(session, source.id, DataType.flow, 250.0)
    cache.update_latest_gauge(session, gauge.id, DataType.flow)
    assert cache.get_latest_gauge(session, gauge.id, DataType.flow) is not None

    session.query(GaugeSource).filter_by(gauge_id=gauge.id).delete()
    session.flush()

    cache.update_latest_gauge(session, gauge.id, DataType.flow)
    assert (
        session.query(LatestGaugeObservation)
        .filter_by(gauge_id=gauge.id, data_type=DataType.flow)
        .count()
        == 0
    )
