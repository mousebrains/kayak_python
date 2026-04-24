"""Gauge lookups and gauge-scoped aggregation helpers."""

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kayak.db.models import DataType, Gauge, GaugeSource, Observation, Source


class GaugeDeletionGuardError(RuntimeError):
    """Raised by :func:`delete_gauge` when safety preconditions aren't met."""


def get_gauge_by_name(session: Session, name: str) -> Gauge | None:
    """Fetch a Gauge by its name."""
    return session.execute(select(Gauge).where(Gauge.name == name)).scalar_one_or_none()


def get_primary_source_id(session: Session, gauge_id: int) -> int | None:
    """Return the source_id of the first GaugeSource for a gauge, or None."""
    gs = session.execute(
        select(GaugeSource.source_id).where(GaugeSource.gauge_id == gauge_id)
    ).scalar()
    return gs


def get_source_ids_for_gauge(session: Session, gauge_id: int) -> list[int]:
    """Return all source_ids linked to a gauge."""
    rows = (
        session.execute(select(GaugeSource.source_id).where(GaugeSource.gauge_id == gauge_id))
        .scalars()
        .all()
    )
    return list(rows)


def get_all_primary_source_ids(
    session: Session,
    gauge_ids: list[int],
) -> dict[int, int]:
    """Return a mapping of gauge_id → first source_id for multiple gauges."""
    if not gauge_ids:
        return {}
    rows = session.execute(
        select(GaugeSource.gauge_id, GaugeSource.source_id).where(
            GaugeSource.gauge_id.in_(gauge_ids)
        )
    ).all()
    # Keep only the first source_id per gauge (same semantics as get_primary_source_id)
    result: dict[int, int] = {}
    for gauge_id, source_id in rows:
        if gauge_id not in result:
            result[gauge_id] = source_id
    return result


def get_calculated_gauge_ids(
    session: Session,
    gauge_ids: list[int],
) -> set[int]:
    """Return gauge_ids where any linked source uses a calc_expression."""
    if not gauge_ids:
        return set()
    rows = (
        session.execute(
            select(GaugeSource.gauge_id)
            .join(Source, GaugeSource.source_id == Source.id)
            .where(GaugeSource.gauge_id.in_(gauge_ids), Source.calc_expression_id.is_not(None))
        )
        .scalars()
        .all()
    )
    return set(rows)


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


def delete_gauge(
    session: Session,
    gauge_id: int,
    *,
    allow_with_sources: bool = False,
    min_stale_days: int = 90,
) -> None:
    """Sanctioned deletion path for a Gauge row.

    Reach-less gauges are NOT deletion candidates on their own — callers
    must pass ``allow_with_sources=True`` only after verifying the gauge
    has no observations newer than ``min_stale_days`` AND no active
    sources (reach linkage is intentionally ignored).

    Raises :class:`GaugeDeletionGuardError` if any precondition fails.
    """
    gauge = session.get(Gauge, gauge_id)
    if gauge is None:
        raise GaugeDeletionGuardError(f"Gauge {gauge_id} not found")

    source_ids = get_source_ids_for_gauge(session, gauge_id)
    if source_ids and not allow_with_sources:
        raise GaugeDeletionGuardError(
            f"Gauge {gauge_id} has {len(source_ids)} source(s); "
            "pass allow_with_sources=True after confirming intent"
        )

    if source_ids:
        cutoff = datetime.now(UTC) - timedelta(days=min_stale_days)
        recent = session.scalar(
            select(func.count())
            .select_from(Observation)
            .where(
                Observation.source_id.in_(source_ids),
                Observation.observed_at >= cutoff,
            )
        )
        if recent:
            raise GaugeDeletionGuardError(
                f"Gauge {gauge_id} has {recent} observations newer than {min_stale_days} days"
            )

    session.delete(gauge)
