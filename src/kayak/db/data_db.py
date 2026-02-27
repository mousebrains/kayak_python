"""Observation storage and query helpers (replaces DataDB.C).

All queries are keyed by source_id (int FK) instead of station name strings.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_upsert
from sqlalchemy.orm import Session

from kayak.db.models import (
    DataType,
    Gauge,
    LatestObservation,
    Observation,
    RatingData,
    Source,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Source / Gauge lookups
# ---------------------------------------------------------------------------

def get_source_by_name(session: Session, name: str) -> Source | None:
    """Fetch a Source by its name."""
    return session.execute(
        select(Source).where(Source.name == name)
    ).scalar_one_or_none()


def get_gauge_by_name(session: Session, name: str) -> Gauge | None:
    """Fetch a Gauge by its name."""
    return session.execute(
        select(Gauge).where(Gauge.name == name)
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Observation storage
# ---------------------------------------------------------------------------

def store_observation(
    session: Session,
    source_id: int,
    data_type: DataType | str,
    when: datetime,
    value: float,
) -> bool:
    """Store a single observation, rejecting invalid data.

    Mirrors DataDB::operator() validation:
    - Rejects timestamps in the future
    - Rejects negative flow values
    """
    if isinstance(data_type, str):
        try:
            data_type = DataType(data_type)
        except ValueError:
            logger.error("Unknown data type: %s", data_type)
            return False

    now = datetime.now(UTC)
    if when.tzinfo is None:
        when_utc = when.replace(tzinfo=UTC)
    else:
        when_utc = when.astimezone(UTC)

    if when_utc > now + timedelta(hours=1):
        logger.warning("Rejecting future timestamp %s for source_id=%d", when, source_id)
        return False

    if data_type == DataType.flow and value < 0:
        logger.error("Rejecting negative flow %s for source_id=%d", value, source_id)
        return False

    stmt = sqlite_upsert(Observation).values(
        source_id=source_id,
        observed_at=when,
        data_type=data_type,
        value=value,
    ).on_conflict_do_update(
        index_elements=["source_id", "observed_at", "data_type"],
        set_={"value": value},
    )
    session.execute(stmt)
    return True


# ---------------------------------------------------------------------------
# Latest observation
# ---------------------------------------------------------------------------

def update_latest(
    session: Session,
    source_id: int,
    data_type: DataType,
) -> None:
    """Recompute the LatestObservation row for a source/type from observations.

    Mirrors DataDB::wrapup() latest-value logic:
    - Latest value is the most recent observation
    - Previous value is the most recent observation > 1 hour before latest
    - delta_per_hour is the hourly rate of change
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

    cutoff = latest_row.observed_at - timedelta(hours=1)
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
        session.add(LatestObservation(
            source_id=source_id,
            data_type=data_type,
            observed_at=latest_row.observed_at,
            value=latest_row.value,
            prev_observed_at=prev_observed_at,
            prev_value=prev_value,
            delta_per_hour=delta,
        ))


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


# ---------------------------------------------------------------------------
# Observation queries
# ---------------------------------------------------------------------------

def get_observations(
    session: Session,
    source_id: int,
    data_type: DataType,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[Observation]:
    """Fetch observation records for a source/type in time range."""
    if since is None:
        since = datetime.now(UTC) - timedelta(days=60)

    stmt = (
        select(Observation)
        .where(
            Observation.source_id == source_id,
            Observation.data_type == data_type,
            Observation.observed_at >= since,
        )
    )
    if until is not None:
        stmt = stmt.where(Observation.observed_at <= until)
    stmt = stmt.order_by(Observation.observed_at.desc())
    return list(session.scalars(stmt))


# ---------------------------------------------------------------------------
# Rating tables
# ---------------------------------------------------------------------------

def get_rating_table(
    session: Session,
    rating_id: int,
) -> list[tuple[float, float]]:
    """Fetch rating table entries sorted by gauge_height_ft."""
    rows = session.execute(
        select(RatingData.gauge_height_ft, RatingData.flow_cfs)
        .where(RatingData.rating_id == rating_id)
        .order_by(RatingData.gauge_height_ft)
    ).all()
    return [(r.gauge_height_ft, r.flow_cfs) for r in rows]


def put_rating_table(
    session: Session,
    rating_id: int,
    entries: list[tuple[float, float]],
) -> None:
    """Store rating table entries for a rating_id."""
    session.query(RatingData).filter(RatingData.rating_id == rating_id).delete()
    for feet, cfs in entries:
        session.add(RatingData(rating_id=rating_id, gauge_height_ft=feet, flow_cfs=cfs))


# ---------------------------------------------------------------------------
# Merge sources
# ---------------------------------------------------------------------------

def merge_sources(
    session: Session,
    target_source_id: int,
    input_source_ids: list[int],
    data_type: DataType,
    since: datetime | None = None,
) -> int:
    """Merge observations from multiple sources into a target.

    Returns count of new rows inserted.
    """
    if since is None:
        since = datetime.now(UTC) - timedelta(days=10)

    count = 0
    for src_id in input_source_ids:
        rows = get_observations(session, src_id, data_type, since=since)
        for row in rows:
            if store_observation(session, target_source_id, data_type, row.observed_at, row.value):
                count += 1
    return count
