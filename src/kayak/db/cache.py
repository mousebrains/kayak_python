"""Latest-observation cache — both source-level and gauge-level rollups."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from kayak.db.models import (
    DataType,
    GaugeSource,
    LatestGaugeObservation,
    LatestObservation,
    Observation,
)

DELTA_LOOKBACK_WINDOW = timedelta(hours=6)
"""How far back to look for a previous observation when computing delta_per_hour."""

GAUGE_CACHE_REBUILD_WINDOW = timedelta(days=30)
"""How far back update_all_latest_gauges scans observations.

The bulk rebuild only needs the most recent observation per (gauge, data_type)
plus one ≥6h prior to compute delta. Scanning beyond a few days is wasted work
that materialises millions of rows in temp B-trees during the window function.
30 days comfortably covers gauges that go silent for a few weeks (NWRFC scrape
flakiness, USBR seasonal projects) while keeping the scan well below the full
multi-month observation history."""


# ---------------------------------------------------------------------------
# Source-level cache
# ---------------------------------------------------------------------------


def update_latest(
    session: Session,
    source_id: int,
    data_type: DataType,
) -> None:
    """Recompute the LatestObservation row for a source/type from observations.

    - Latest value is the most recent observation
    - Previous value is the most recent observation > 6 hours before latest
    - delta_per_hour is the hourly rate of change over that window
    """
    latest_row = session.execute(
        select(Observation)
        .where(
            Observation.source_id == source_id,
            Observation.data_type == data_type,
        )
        .order_by(Observation.observed_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if latest_row is None:
        session.execute(
            delete(LatestObservation).where(
                LatestObservation.source_id == source_id,
                LatestObservation.data_type == data_type,
            )
        )
        return

    cutoff = latest_row.observed_at - DELTA_LOOKBACK_WINDOW
    prev_row = session.execute(
        select(Observation)
        .where(
            Observation.source_id == source_id,
            Observation.data_type == data_type,
            Observation.observed_at <= cutoff,
        )
        .order_by(Observation.observed_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    delta = None
    prev_observed_at = None
    prev_value = None
    if prev_row is not None:
        prev_observed_at = prev_row.observed_at
        prev_value = prev_row.value
        hours_diff = (latest_row.observed_at - prev_row.observed_at).total_seconds() / 3600
        if hours_diff > 0:
            delta = (latest_row.value - prev_row.value) / hours_diff

    existing = session.execute(
        select(LatestObservation).where(
            LatestObservation.source_id == source_id,
            LatestObservation.data_type == data_type,
        )
    ).scalar_one_or_none()

    if existing:
        existing.observed_at = latest_row.observed_at
        existing.value = latest_row.value
        existing.prev_observed_at = prev_observed_at
        existing.prev_value = prev_value
        existing.delta_per_hour = delta
    else:
        session.add(
            LatestObservation(
                source_id=source_id,
                data_type=data_type,
                observed_at=latest_row.observed_at,
                value=latest_row.value,
                prev_observed_at=prev_observed_at,
                prev_value=prev_value,
                delta_per_hour=delta,
            )
        )


def get_latest(
    session: Session,
    source_id: int,
    data_type: DataType,
) -> LatestObservation | None:
    """Fetch the LatestObservation row for a source/type."""
    return session.execute(
        select(LatestObservation).where(
            LatestObservation.source_id == source_id,
            LatestObservation.data_type == data_type,
        )
    ).scalar_one_or_none()


def get_all_latest(
    session: Session,
    source_ids: list[int],
) -> dict[tuple[int, DataType], LatestObservation]:
    """Fetch all LatestObservation rows for a list of source_ids.

    Returns a dict keyed by (source_id, data_type).
    """
    if not source_ids:
        return {}
    rows = session.scalars(
        select(LatestObservation).where(LatestObservation.source_id.in_(source_ids))
    ).all()
    return {(r.source_id, r.data_type): r for r in rows}


# ---------------------------------------------------------------------------
# Gauge-level cache
# ---------------------------------------------------------------------------


def update_latest_gauge(
    session: Session,
    gauge_id: int,
    data_type: DataType,
) -> None:
    """Recompute the LatestGaugeObservation for a gauge/type.

    Finds the most recent observation across ALL sources linked to the gauge,
    computes delta_per_hour from a previous observation >6h before latest,
    and upserts into the latest_gauge_observation cache.
    """
    source_ids = list(
        session.scalars(select(GaugeSource.source_id).where(GaugeSource.gauge_id == gauge_id))
    )
    if not source_ids:
        session.execute(
            delete(LatestGaugeObservation).where(
                LatestGaugeObservation.gauge_id == gauge_id,
                LatestGaugeObservation.data_type == data_type,
            )
        )
        return

    latest_row = session.execute(
        select(Observation)
        .where(
            Observation.source_id.in_(source_ids),
            Observation.data_type == data_type,
        )
        .order_by(Observation.observed_at.desc(), Observation.source_id.desc())
        .limit(1)
    ).scalar_one_or_none()

    if latest_row is None:
        session.execute(
            delete(LatestGaugeObservation).where(
                LatestGaugeObservation.gauge_id == gauge_id,
                LatestGaugeObservation.data_type == data_type,
            )
        )
        return

    cutoff = latest_row.observed_at - DELTA_LOOKBACK_WINDOW
    prev_row = session.execute(
        select(Observation)
        .where(
            Observation.source_id.in_(source_ids),
            Observation.data_type == data_type,
            Observation.observed_at <= cutoff,
        )
        .order_by(Observation.observed_at.desc(), Observation.source_id.desc())
        .limit(1)
    ).scalar_one_or_none()

    delta = None
    prev_observed_at = None
    prev_value = None
    if prev_row is not None:
        prev_observed_at = prev_row.observed_at
        prev_value = prev_row.value
        hours_diff = (latest_row.observed_at - prev_row.observed_at).total_seconds() / 3600
        if hours_diff > 0:
            delta = (latest_row.value - prev_row.value) / hours_diff

    existing = session.execute(
        select(LatestGaugeObservation).where(
            LatestGaugeObservation.gauge_id == gauge_id,
            LatestGaugeObservation.data_type == data_type,
        )
    ).scalar_one_or_none()

    if existing:
        existing.observed_at = latest_row.observed_at
        existing.value = latest_row.value
        existing.prev_observed_at = prev_observed_at
        existing.prev_value = prev_value
        existing.delta_per_hour = delta
        existing.source_id = latest_row.source_id
    else:
        session.add(
            LatestGaugeObservation(
                gauge_id=gauge_id,
                data_type=data_type,
                observed_at=latest_row.observed_at,
                value=latest_row.value,
                prev_observed_at=prev_observed_at,
                prev_value=prev_value,
                delta_per_hour=delta,
                source_id=latest_row.source_id,
            )
        )


_BULK_REBUILD_GAUGE_CACHE_SQL = """
WITH ranked AS (
    SELECT
        gs.gauge_id        AS gauge_id,
        o.data_type        AS data_type,
        o.source_id        AS source_id,
        o.observed_at      AS observed_at,
        o.value            AS value,
        ROW_NUMBER() OVER (
            PARTITION BY gs.gauge_id, o.data_type
            ORDER BY o.observed_at DESC, o.source_id DESC
        ) AS rn
    FROM observation o
    JOIN gauge_source gs ON gs.source_id = o.source_id
    WHERE o.observed_at >= :since
),
latest_pick AS (
    SELECT gauge_id, data_type, source_id, observed_at, value
    FROM ranked
    WHERE rn = 1
),
prev_ranked AS (
    SELECT
        gs.gauge_id   AS gauge_id,
        o.data_type   AS data_type,
        o.observed_at AS observed_at,
        o.value       AS value,
        ROW_NUMBER() OVER (
            PARTITION BY gs.gauge_id, o.data_type
            ORDER BY o.observed_at DESC, o.source_id DESC
        ) AS rn
    FROM observation o
    JOIN gauge_source gs ON gs.source_id = o.source_id
    JOIN latest_pick lp
        ON lp.gauge_id = gs.gauge_id
       AND lp.data_type = o.data_type
       AND o.observed_at <= datetime(lp.observed_at, '-6 hours')
    WHERE o.observed_at >= :since
),
prev_pick AS (
    SELECT gauge_id, data_type, observed_at, value
    FROM prev_ranked
    WHERE rn = 1
)
INSERT INTO latest_gauge_observation
    (gauge_id, data_type, observed_at, value,
     prev_observed_at, prev_value, delta_per_hour, source_id)
SELECT
    lp.gauge_id,
    lp.data_type,
    lp.observed_at,
    lp.value,
    pp.observed_at,
    pp.value,
    CASE
        WHEN pp.observed_at IS NULL THEN NULL
        WHEN (julianday(lp.observed_at) - julianday(pp.observed_at)) * 24 = 0 THEN NULL
        ELSE (lp.value - pp.value)
             / ((julianday(lp.observed_at) - julianday(pp.observed_at)) * 24)
    END,
    lp.source_id
FROM latest_pick lp
LEFT JOIN prev_pick pp
    ON pp.gauge_id = lp.gauge_id AND pp.data_type = lp.data_type
"""


def update_all_latest_gauges(session: Session, since: datetime | None = None) -> None:
    """Recompute latest_gauge_observation for every gauge in one bulk SQL.

    Wipes the cache table inside the same transaction and rebuilds it from
    ``observation`` joined to ``gauge_source`` using window functions.
    Equivalent row-for-row to looping ``update_latest_gauge`` over every
    (gauge_id, data_type) pair *for observations newer than ``since``*, but
    ~3 SQL statements total instead of ~5*4*N for N gauges with sources.
    Verified by ``test_bulk_matches_per_gauge_loop``.

    ``since`` defaults to ``now - GAUGE_CACHE_REBUILD_WINDOW`` (30 days).
    Older observations are excluded from the window function so the scan
    stays bounded as the observation history grows. Gauges silent longer
    than ``since`` get no cache row — the display layer treats that as
    "no recent data", which is the right outcome for a stale gauge.

    Tiebreaker on identical ``observed_at`` is ``source_id DESC`` —
    deterministic across runs and matching ``update_latest_gauge``, which
    applies the same ``observed_at DESC, source_id DESC`` ordering (review-4 R5.1).
    """
    if since is None:
        since = datetime.now(UTC) - GAUGE_CACHE_REBUILD_WINDOW
    session.execute(delete(LatestGaugeObservation))
    session.execute(text(_BULK_REBUILD_GAUGE_CACHE_SQL), {"since": since})
    session.commit()


def get_latest_gauge(
    session: Session,
    gauge_id: int,
    data_type: DataType,
) -> LatestGaugeObservation | None:
    """Fetch the LatestGaugeObservation row for a gauge/type."""
    return session.execute(
        select(LatestGaugeObservation).where(
            LatestGaugeObservation.gauge_id == gauge_id,
            LatestGaugeObservation.data_type == data_type,
        )
    ).scalar_one_or_none()


def get_all_latest_gauges(
    session: Session,
    gauge_ids: list[int],
) -> dict[tuple[int, DataType], LatestGaugeObservation]:
    """Fetch all LatestGaugeObservation rows for a list of gauge_ids.

    Returns a dict keyed by (gauge_id, data_type).
    """
    if not gauge_ids:
        return {}
    rows = session.scalars(
        select(LatestGaugeObservation).where(LatestGaugeObservation.gauge_id.in_(gauge_ids))
    ).all()
    return {(r.gauge_id, r.data_type): r for r in rows}
