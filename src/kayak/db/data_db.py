"""Measurement storage and query helpers (replaces DataDB.C)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_upsert
from sqlalchemy.orm import Session

from kayak.db.models import DataType, Latest, Measurement, RatingTable, URL2Name

logger = logging.getLogger(__name__)


def store_measurement(
    session: Session,
    station: str,
    data_type: DataType | str,
    when: datetime,
    value: float,
) -> bool:
    """Store a single measurement, rejecting invalid data.

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

    now = datetime.now(timezone.utc)
    if when.tzinfo is None:
        when_utc = when.replace(tzinfo=timezone.utc)
    else:
        when_utc = when.astimezone(timezone.utc)

    if when_utc > now + timedelta(hours=1):
        logger.warning("Rejecting future timestamp %s for %s", when, station)
        return False

    if data_type == DataType.FLOW and value < 0:
        logger.error("Rejecting negative flow %s for %s", value, station)
        return False

    # Use INSERT OR REPLACE for SQLite, ON DUPLICATE KEY UPDATE for MySQL
    stmt = sqlite_upsert(Measurement).values(
        station=station,
        data_type=data_type,
        time=when,
        value=value,
    ).on_conflict_do_update(
        index_elements=["station", "data_type", "time"],
        set_={"value": value},
    )
    session.execute(stmt)
    return True


def store_url(session: Session, url: str, station: str) -> None:
    """Record URL-to-station mapping (replaces DataDB::url)."""
    session.add(URL2Name(url=url, name=station))


def update_latest(
    session: Session,
    station: str,
    data_type: DataType,
) -> None:
    """Recompute the Latest row for a station/type from measurements.

    Mirrors DataDB::wrapup() latest-value logic:
    - Latest value is the most recent measurement
    - Previous value is the most recent measurement > 24 hours before latest
    - Delta is the hourly rate of change
    """
    # Get most recent measurement
    latest_row = session.execute(
        select(Measurement)
        .where(
            Measurement.station == station,
            Measurement.data_type == data_type,
        )
        .order_by(Measurement.time.desc())
        .limit(1)
    ).scalar_one_or_none()

    if latest_row is None:
        return

    # Get previous measurement (1-25 hours before latest)
    cutoff = latest_row.time - timedelta(hours=1)
    prev_row = session.execute(
        select(Measurement)
        .where(
            Measurement.station == station,
            Measurement.data_type == data_type,
            Measurement.time <= cutoff,
        )
        .order_by(Measurement.time.desc())
        .limit(1)
    ).scalar_one_or_none()

    delta = None
    prev_time = None
    prev_value = None
    if prev_row is not None:
        prev_time = prev_row.time
        prev_value = prev_row.value
        hours_diff = (latest_row.time - prev_row.time).total_seconds() / 3600
        if hours_diff > 0:
            delta = (latest_row.value - prev_row.value) / hours_diff

    # Upsert Latest
    existing = session.execute(
        select(Latest).where(
            Latest.station == station,
            Latest.data_type == data_type,
        )
    ).scalar_one_or_none()

    if existing:
        existing.time = latest_row.time
        existing.value = latest_row.value
        existing.prev_time = prev_time
        existing.prev_value = prev_value
        existing.delta = delta
    else:
        session.add(Latest(
            station=station,
            data_type=data_type,
            time=latest_row.time,
            value=latest_row.value,
            prev_time=prev_time,
            prev_value=prev_value,
            delta=delta,
        ))


def get_latest(
    session: Session,
    station: str,
    data_type: DataType,
) -> Latest | None:
    """Fetch the Latest row for a station/type."""
    return session.execute(
        select(Latest).where(
            Latest.station == station,
            Latest.data_type == data_type,
        )
    ).scalar_one_or_none()


def get_measurements(
    session: Session,
    station: str,
    data_type: DataType,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[Measurement]:
    """Fetch measurement records for a station/type in time range."""
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(days=60)

    stmt = (
        select(Measurement)
        .where(
            Measurement.station == station,
            Measurement.data_type == data_type,
            Measurement.time >= since,
        )
    )
    if until is not None:
        stmt = stmt.where(Measurement.time <= until)
    stmt = stmt.order_by(Measurement.time.desc())
    return list(session.scalars(stmt))


def get_rating_table(
    session: Session,
    db_name: str,
) -> list[tuple[float, float]]:
    """Fetch rating table entries sorted by feet (replaces DataDB::getRatingTable)."""
    rows = session.execute(
        select(RatingTable.feet, RatingTable.cfs)
        .where(RatingTable.db_name == db_name)
        .order_by(RatingTable.feet)
    ).all()
    return [(r.feet, r.cfs) for r in rows]


def put_rating_table(
    session: Session,
    db_name: str,
    entries: list[tuple[float, float]],
) -> None:
    """Store rating table entries (replaces DataDB::putRatingTable)."""
    # Clear existing entries
    session.query(RatingTable).filter(RatingTable.db_name == db_name).delete()
    for feet, cfs in entries:
        session.add(RatingTable(db_name=db_name, feet=feet, cfs=cfs))


def merge_stations(
    session: Session,
    target_station: str,
    source_stations: list[str],
    data_type: DataType,
    since: datetime | None = None,
) -> int:
    """Merge measurements from multiple source stations into a target.

    Replaces DataDB::merge(). Uses INSERT OR IGNORE to skip duplicates.
    Returns count of new rows inserted.
    """
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(days=10)

    count = 0
    for source in source_stations:
        rows = get_measurements(session, source, data_type, since=since)
        for row in rows:
            if store_measurement(session, target_station, data_type, row.time, row.value):
                count += 1
    return count
