"""Reach/Gauge/State query helpers (replaces InfoDB.C).

Uses the normalized schema with Reach, Gauge, State, and junction tables
instead of the flat Master/MergedMaster approach.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from kayak.db.models import DataType, FlowLevel, Gauge, GaugeSource, Reach, Source, State


def all_states(session: Session) -> list[State]:
    """Return all State records sorted by name."""
    return list(session.scalars(select(State).order_by(State.name)))


def all_state_names(session: Session) -> list[str]:
    """Return sorted list of state names that have visible reaches."""
    rows = (
        session.execute(
            select(State.name)
            .join(State.reaches)
            .where(Reach.no_show.is_(False))
            .group_by(State.name)
            .order_by(State.name)
        )
        .scalars()
        .all()
    )
    return list(rows)


def reaches_query(
    session: Session,
    *,
    state_name: str | None = None,
    visible_only: bool = True,
    with_gauge: bool = False,
    sort_by_state: bool = False,
) -> list[Reach]:
    """Query reaches with optional filtering.

    Args:
        state_name: Filter by state name if provided
        visible_only: Exclude no_show reaches (default True)
        with_gauge: Eagerly load gauge relationship
        sort_by_state: Sort by (state, sort_name) instead of just sort_name
    """
    if sort_by_state:
        # Multi-state reaches sort by their first state alphabetically;
        # .distinct() prevents duplicate rows from the join.
        stmt = select(Reach).join(Reach.states).order_by(State.name, Reach.sort_name).distinct()
    else:
        stmt = select(Reach).order_by(Reach.sort_name)

    if visible_only:
        stmt = stmt.where(Reach.no_show.is_(False))

    if with_gauge:
        stmt = stmt.options(
            joinedload(Reach.gauge),
            selectinload(Reach.states),
            selectinload(Reach.classes),
            selectinload(Reach.levels),
        )

    if state_name:
        stmt = stmt.join(Reach.states).where(State.name == state_name)

    return list(session.scalars(stmt))


def get_reach(session: Session, reach_id: int) -> Reach | None:
    """Fetch a Reach by ID."""
    return session.get(Reach, reach_id)


def get_reach_by_name(session: Session, name: str) -> Reach | None:
    """Fetch a Reach by its unique name."""
    return session.execute(select(Reach).where(Reach.name == name)).scalar_one_or_none()


def display_name(session: Session, reach_id: int) -> str | None:
    """Get display_name for a reach ID."""
    row = session.get(Reach, reach_id)
    return row.display_name if row else None


def get_gauge_for_reach(session: Session, reach_id: int) -> Gauge | None:
    """Get the Gauge associated with a Reach."""
    reach = session.get(Reach, reach_id)
    if reach is None or reach.gauge_id is None:
        return None
    return session.get(Gauge, reach.gauge_id)


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


def is_source_calculated(session: Session, source_id: int) -> bool:
    """Return True if the source uses a calc_expression instead of a fetch URL."""
    src = session.get(Source, source_id)
    return src is not None and src.calc_expression_id is not None


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


def get_calculated_source_ids(
    session: Session,
    source_ids: list[int],
) -> set[int]:
    """Return the subset of source_ids that use calc_expression (estimated)."""
    if not source_ids:
        return set()
    rows = (
        session.execute(
            select(Source.id).where(
                Source.id.in_(source_ids), Source.calc_expression_id.is_not(None)
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


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


def classify_level(
    reach: Reach,
    data_type: DataType,
    value: float,
) -> FlowLevel | None:
    """Return the FlowLevel for a value given a reach's level ranges.

    Checks the reach.levels list for a matching range where the
    data_type matches and value falls within [low, high].
    """
    for sl in reach.levels:
        # Match on data_type — low_data_type and high_data_type should
        # both match the queried type (when set).
        if sl.low_data_type and sl.low_data_type != data_type:
            continue
        if sl.high_data_type and sl.high_data_type != data_type:
            continue
        low = sl.low if sl.low is not None else float("-inf")
        high = sl.high if sl.high is not None else float("inf")
        if low <= value <= high:
            return sl.level
    return None
