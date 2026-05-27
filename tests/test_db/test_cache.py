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
            # Inside the gauge-cache window so update_latest_gauge's `since` bound
            # (R5.7) sees it; the prune/unlink tests then assert it later vanishes.
            observed_at=datetime.now(UTC) - timedelta(hours=1),
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


def test_update_latest_gauge_ignores_observations_older_than_since(session, linked_source_gauge):
    """A gauge whose newest observation predates the window gets no cache row:
    update_latest_gauge applies the same `since` bound as the bulk rebuild, so both
    agree a long-silent gauge has no recent data (review-4 R5.7). An explicit wider
    `since` still sees it -- the bound is configurable, not hard-coded."""
    source, gauge = linked_source_gauge
    old = datetime.now(UTC) - timedelta(days=400)  # well outside the 30-day window
    session.add(
        Observation(source_id=source.id, data_type=DataType.flow, observed_at=old, value=42.0)
    )
    session.flush()

    # Default window: the stale observation is filtered out -> no cache row.
    cache.update_latest_gauge(session, gauge.id, DataType.flow)
    assert cache.get_latest_gauge(session, gauge.id, DataType.flow) is None

    # Explicit wide window: the same observation is now in range -> cache row built.
    cache.update_latest_gauge(
        session, gauge.id, DataType.flow, since=datetime.now(UTC) - timedelta(days=500)
    )
    assert cache.get_latest_gauge(session, gauge.id, DataType.flow) is not None


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

    fu = FetchUrl(url="https://example.com/seed", parser="nwps", is_active=True)
    session.add(fu)
    session.flush()

    # Anchor to "now" so the seed stays inside update_all_latest_gauges's
    # 30-day rebuild window regardless of when the suite runs.
    base = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)

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


def test_update_latest_gauge_tiebreaks_on_higher_source_id(session):
    """Two sources of one gauge reporting the same observed_at must resolve to
    the higher source_id's value, matching the bulk rebuild's
    ``observed_at DESC, source_id DESC`` (review-4 R5.1). Without the tiebreak
    the per-gauge ``LIMIT 1`` was unordered on the tie and could return either."""
    from kayak.db.models import FetchUrl, Gauge, GaugeSource, Source

    fu = FetchUrl(url="https://example.com/tie", parser="nwps", is_active=True)
    session.add(fu)
    session.flush()
    gauge = Gauge(name="tie-gauge", usgs_id="9999001")
    session.add(gauge)
    session.flush()
    # Anchor inside the bulk path's 30-day rebuild window.
    t = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)
    src_ids: list[int] = []
    for val in (100.0, 200.0):  # second (200) gets the higher autoincrement id
        src = Source(name=f"tie-src-{val}", agency="USGS", fetch_url_id=fu.id)
        session.add(src)
        session.flush()
        session.add(GaugeSource(gauge_id=gauge.id, source_id=src.id))
        session.add(
            Observation(source_id=src.id, data_type=DataType.flow, observed_at=t, value=val)
        )
        src_ids.append(src.id)
    session.flush()
    higher = max(src_ids)

    cache.update_latest_gauge(session, gauge.id, DataType.flow)
    row = (
        session.query(LatestGaugeObservation)
        .filter_by(gauge_id=gauge.id, data_type=DataType.flow)
        .one()
    )
    assert row.source_id == higher, "tie must resolve to the higher source_id"
    assert row.value == 200.0

    # The bulk rebuild must agree — the contract the docstring promises.
    session.query(LatestGaugeObservation).delete()
    session.flush()
    cache.update_all_latest_gauges(session)
    bulk_row = (
        session.query(LatestGaugeObservation)
        .filter_by(gauge_id=gauge.id, data_type=DataType.flow)
        .one()
    )
    assert bulk_row.source_id == higher
    assert bulk_row.value == 200.0


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
