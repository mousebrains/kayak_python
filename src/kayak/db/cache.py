"""Latest-observation cache — both source-level and gauge-level rollups."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from kayak.db.models import (
    DataType,
    Gauge,
    GaugeSource,
    LatestGaugeObservation,
    LatestObservation,
    Observation,
)

DELTA_LOOKBACK_WINDOW = timedelta(hours=6)
"""How far back to look for a previous observation when computing delta_per_hour."""


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
        return

    latest_row = session.execute(
        select(Observation)
        .where(
            Observation.source_id.in_(source_ids),
            Observation.data_type == data_type,
        )
        .order_by(Observation.observed_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if latest_row is None:
        return

    cutoff = latest_row.observed_at - DELTA_LOOKBACK_WINDOW
    prev_row = session.execute(
        select(Observation)
        .where(
            Observation.source_id.in_(source_ids),
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


def update_all_latest_gauges(session: Session) -> None:
    """Recompute latest_gauge_observation for all gauges and data types."""
    gauge_ids = list(
        session.scalars(
            select(Gauge.id).where(Gauge.id.in_(select(GaugeSource.gauge_id).distinct()))
        )
    )
    types = [DataType.flow, DataType.inflow, DataType.gauge, DataType.temperature]
    for gauge_id in gauge_ids:
        for dtype in types:
            update_latest_gauge(session, gauge_id, dtype)
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
