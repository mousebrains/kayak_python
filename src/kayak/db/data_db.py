"""Observation storage and query helpers (replaces DataDB.C).

All queries are keyed by source_id (int FK) instead of station name strings.
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_upsert
from sqlalchemy.orm import Session

from kayak.db.models import (
    DataType,
    Gauge,
    GaugeSource,
    LatestGaugeObservation,
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
    return session.execute(select(Source).where(Source.name == name)).scalar_one_or_none()


def get_gauge_by_name(session: Session, name: str) -> Gauge | None:
    """Fetch a Gauge by its name."""
    return session.execute(select(Gauge).where(Gauge.name == name)).scalar_one_or_none()


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

    when = when.replace(microsecond=0)
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

    stmt = (
        sqlite_upsert(Observation)
        .values(
            source_id=source_id,
            observed_at=when,
            data_type=data_type,
            value=value,
        )
        .on_conflict_do_update(
            index_elements=["source_id", "observed_at", "data_type"],
            set_={"value": value},
        )
    )
    session.execute(stmt)
    return True


def store_observations(session: Session, values: list[dict]) -> int:
    """Store multiple observations in a single batch INSERT.

    Each dict must have keys: source_id, data_type, observed_at, value.
    Same validation rules as store_observation(): rejects future timestamps
    and negative flow values. Returns count of rows stored.
    """
    now = datetime.now(UTC)
    future_cutoff = now + timedelta(hours=1)
    valid_rows = []

    for row in values:
        data_type = row["data_type"]
        if isinstance(data_type, str):
            try:
                data_type = DataType(data_type)
            except ValueError:
                logger.error("Unknown data type: %s", data_type)
                continue

        when = row["observed_at"].replace(microsecond=0)
        if when.tzinfo is None:
            when_utc = when.replace(tzinfo=UTC)
        else:
            when_utc = when.astimezone(UTC)

        if when_utc > future_cutoff:
            logger.warning(
                "Rejecting future timestamp %s for source_id=%d",
                when,
                row["source_id"],
            )
            continue

        value = row["value"]
        if data_type == DataType.flow and value < 0:
            logger.error(
                "Rejecting negative flow %s for source_id=%d",
                value,
                row["source_id"],
            )
            continue

        valid_rows.append(
            {
                "source_id": row["source_id"],
                "data_type": data_type,
                "observed_at": when,
                "value": value,
            }
        )

    if not valid_rows:
        return 0

    # SQLite has a 999-variable limit; each row uses 4 variables → batch at 200
    BATCH = 200
    for i in range(0, len(valid_rows), BATCH):
        batch = valid_rows[i : i + BATCH]
        stmt = (
            sqlite_upsert(Observation)
            .values(batch)
            .on_conflict_do_update(
                index_elements=["source_id", "observed_at", "data_type"],
                set_={"value": sqlite_upsert(Observation).excluded.value},
            )
        )
        session.execute(stmt)
    return len(valid_rows)


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

    cutoff = latest_row.observed_at - timedelta(hours=6)
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
# Gauge-level latest cache
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

    cutoff = latest_row.observed_at - timedelta(hours=6)
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


def get_bulk_gauge_observations(
    session: Session,
    gauge_ids: list[int],
    data_type: DataType,
    since: datetime,
) -> dict[int, list[Observation]]:
    """Fetch observations for multiple gauges, combining all sources per gauge.

    Returns a dict keyed by gauge_id with lists of Observation sorted
    descending by observed_at.
    """
    if not gauge_ids:
        return {}
    # Build gauge_id → [source_ids] mapping
    gs_rows = session.execute(
        select(GaugeSource.gauge_id, GaugeSource.source_id).where(
            GaugeSource.gauge_id.in_(gauge_ids)
        )
    ).all()
    gauge_to_sources: dict[int, list[int]] = defaultdict(list)
    source_to_gauge: dict[int, int] = {}
    for gid, sid in gs_rows:
        gauge_to_sources[gid].append(sid)
        source_to_gauge[sid] = gid

    all_source_ids = list(source_to_gauge.keys())
    if not all_source_ids:
        return {}

    stmt = (
        select(Observation)
        .where(
            Observation.source_id.in_(all_source_ids),
            Observation.data_type == data_type,
            Observation.observed_at >= since,
        )
        .order_by(Observation.observed_at.desc())
    )
    rows = list(session.scalars(stmt))
    result: dict[int, list[Observation]] = defaultdict(list)
    for row in rows:
        gid = source_to_gauge[row.source_id]
        result[gid].append(row)
    return dict(result)


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

    stmt = select(Observation).where(
        Observation.source_id == source_id,
        Observation.data_type == data_type,
        Observation.observed_at >= since,
    )
    if until is not None:
        stmt = stmt.where(Observation.observed_at <= until)
    stmt = stmt.order_by(Observation.observed_at.desc())
    return list(session.scalars(stmt))


def get_bulk_observations(
    session: Session,
    source_ids: list[int],
    data_type: DataType,
    since: datetime,
) -> dict[int, list[Observation]]:
    """Fetch observations for multiple sources in a single query.

    Returns a dict keyed by source_id with lists of Observation sorted
    descending by observed_at.
    """
    if not source_ids:
        return {}
    stmt = (
        select(Observation)
        .where(
            Observation.source_id.in_(source_ids),
            Observation.data_type == data_type,
            Observation.observed_at >= since,
        )
        .order_by(Observation.source_id, Observation.observed_at.desc())
    )
    rows = list(session.scalars(stmt))
    result: dict[int, list[Observation]] = defaultdict(list)
    for row in rows:
        result[row.source_id].append(row)
    return dict(result)


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
    window_minutes: int = 15,
) -> int:
    """Merge observations from multiple sources into a target.

    For each output timestamp, collects all observations from all input
    sources within ±window_minutes and computes the median. This smooths
    out noise from measurement jitter and small offsets between sources.

    Returns count of new rows inserted.
    """
    if since is None:
        since = datetime.now(UTC) - timedelta(days=10)

    window = timedelta(minutes=window_minutes)

    # Collect all (timestamp, value) pairs from all input sources.
    # Normalize timestamps to second precision so that e.g. "00:00:00"
    # and "00:00:00.000000" collapse to the same output point.
    all_obs: list[tuple[datetime, float]] = []
    for src_id in input_source_ids:
        obs_rows = get_observations(session, src_id, data_type, since=since)
        for row in obs_rows:
            t = row.observed_at.replace(microsecond=0)
            all_obs.append((t, row.value))

    if not all_obs:
        return 0

    all_obs.sort(key=lambda x: x[0])

    # Collect unique timestamps as output points
    timestamps = sorted({t for t, _ in all_obs})

    rows = []
    # Use two pointers to find values within the window for each timestamp
    n = len(all_obs)
    lo = 0
    for observed_at in timestamps:
        win_start = observed_at - window
        win_end = observed_at + window

        # Advance lo pointer past expired entries
        while lo < n and all_obs[lo][0] < win_start:
            lo += 1

        # Collect values in window
        vals = []
        for i in range(lo, n):
            if all_obs[i][0] > win_end:
                break
            vals.append(all_obs[i][1])

        if vals:
            rows.append(
                {
                    "source_id": target_source_id,
                    "data_type": data_type,
                    "observed_at": observed_at,
                    "value": round(statistics.median(vals), 2),
                }
            )

    # Delete the target's existing observations in this range before writing,
    # so stale rows from previous merges or fetches don't linger.
    session.execute(
        delete(Observation).where(
            Observation.source_id == target_source_id,
            Observation.data_type == data_type,
            Observation.observed_at >= since,
        )
    )

    return store_observations(session, rows)
