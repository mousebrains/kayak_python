"""Cache pruning: latest_* rows must disappear when backing observations vanish.

Regression for the stale-cache drift observed in the live DB (2026-04-23):
`update_latest` / `update_latest_gauge` returned early when the source had no
observations, leaving orphaned rows that outlived the raw series that produced
them (e.g. after `decimate` purged everything older than retention).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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


def test_update_latest_gauge_prunes_stale_row_when_observations_gone(session, linked_source_gauge):
    source, gauge = linked_source_gauge
    _add_obs(session, source.id, DataType.flow, 250.0)
    cache.update_latest_gauge(session, gauge.id, DataType.flow)
    assert cache.get_latest_gauge(session, gauge.id, DataType.flow) is not None

    session.query(Observation).filter_by(source_id=source.id).delete()
    session.flush()

    cache.update_latest_gauge(session, gauge.id, DataType.flow)
    assert cache.get_latest_gauge(session, gauge.id, DataType.flow) is None


def test_update_latest_gauge_prunes_stale_row_when_sources_unlinked(session, linked_source_gauge):
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


# ---------------------------------------------------------------------------
# update_all_latest_gauges — bulk path
# ---------------------------------------------------------------------------


def _seed_multi_gauge_observations(session) -> None:
    """Three gauges, two sources each, two data types, three observations each.

    Used by the bulk-vs-loop equivalence test to exercise gauge-spanning
    multi-source aggregation, multi-type partitioning, and the 6h prev
    window.
    """
    from kayak.db.models import FetchUrl, Gauge, GaugeSource, Source

    fu = FetchUrl(url="https://example.com/seed", parser="usgs", is_active=True)
    session.add(fu)
    session.flush()

    base = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)

    for gi in range(3):
        gauge = Gauge(name=f"g{gi}", usgs_id=f"100000{gi}")
        session.add(gauge)
        session.flush()
        for si in range(2):
            source = Source(name=f"src-{gi}-{si}", agency="USGS", fetch_url_id=fu.id)
            session.add(source)
            session.flush()
            session.add(GaugeSource(gauge_id=gauge.id, source_id=source.id))
            for dtype in (DataType.flow, DataType.gauge):
                # 3 obs spaced 7h apart so the 6h "prev" window catches the
                # second-newest, but the third sits outside it.
                for k in range(3):
                    session.add(
                        Observation(
                            source_id=source.id,
                            data_type=dtype,
                            observed_at=base - timedelta(hours=7 * k),
                            value=10.0 * gi + 0.1 * si + 0.01 * k,
                        )
                    )
    session.flush()


def _snapshot_gauge_cache(session) -> list[tuple]:
    """Return a sorted snapshot of every latest_gauge_observation row.

    Materialised as plain tuples so equality works regardless of identity
    (ORM objects don't compare structurally) and so the rows are hashable
    for ``sorted()``.
    """
    rows = session.query(LatestGaugeObservation).all()
    return sorted(
        (
            r.gauge_id,
            r.data_type,
            r.observed_at,
            r.value,
            r.prev_observed_at,
            r.prev_value,
            None if r.delta_per_hour is None else round(r.delta_per_hour, 6),
            r.source_id,
        )
        for r in rows
    )


def test_bulk_matches_per_gauge_loop(session):
    """update_all_latest_gauges (bulk SQL) produces the same rows as
    looping update_latest_gauge over every (gauge_id, data_type) pair."""
    from sqlalchemy import select as sa_select

    from kayak.db.models import GaugeSource as GS

    _seed_multi_gauge_observations(session)

    # Bulk path
    cache.update_all_latest_gauges(session)
    bulk = _snapshot_gauge_cache(session)

    # Wipe and rebuild via the per-gauge loop
    session.query(LatestGaugeObservation).delete()
    session.flush()
    gauge_ids = list(session.scalars(sa_select(GS.gauge_id).distinct()))
    for gid in gauge_ids:
        for dt in (DataType.flow, DataType.gauge, DataType.inflow, DataType.temperature):
            cache.update_latest_gauge(session, gid, dt)
    looped = _snapshot_gauge_cache(session)

    assert bulk == looped, (
        f"bulk and per-gauge loop disagree:\n  bulk:   {bulk}\n  looped: {looped}"
    )
    # Each gauge has flow + gauge data: 3 gauges * 2 types = 6 cache rows
    assert len(bulk) == 6


def test_bulk_uses_constant_query_count(session):
    """update_all_latest_gauges should issue O(1) queries regardless of gauge
    count — proving the N+1 in the previous loop-of-update_latest_gauge
    implementation is gone."""
    from sqlalchemy import event as sa_event

    _seed_multi_gauge_observations(session)

    counter = {"n": 0}

    def _count(_conn, _cursor, _statement, _params, _ctx, _exec_many):
        counter["n"] += 1

    # Listen on the Engine, not the Connection — savepoint releases would
    # otherwise inflate the count with internal SQLAlchemy traffic.
    engine = session.get_bind()
    sa_event.listen(engine, "before_cursor_execute", _count)
    try:
        cache.update_all_latest_gauges(session)
    finally:
        sa_event.remove(engine, "before_cursor_execute", _count)

    # Three gauges x four data types would have been ~60+ queries on the
    # old per-gauge-loop path; the bulk path emits a small handful.
    assert counter["n"] <= 6, f"expected <=6 statements, got {counter['n']}"
