"""Reach / state catalog helpers and flow-level classification."""

from collections.abc import Iterable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, joinedload, selectinload

from kayak.db.models import DataType, FlowLevel, Gauge, Reach, State


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


def iter_reaches_with_putin(session: Session) -> Iterable[Reach]:
    """Yield every reach that has both put-in latitude and longitude set."""
    return session.scalars(
        select(Reach).where(
            Reach.latitude_start.isnot(None),
            Reach.longitude_start.isnot(None),
        )
    )


def set_reach_huc(session: Session, reach_id: int, huc12: str, basin: str | None = None) -> None:
    """Overwrite ``reach.huc`` (and optionally ``reach.basin``) for one reach.

    Pass ``basin`` to also set the WBD-derived HUC8 name into the basin column;
    omit it to leave basin untouched.
    """
    values: dict[str, str] = {"huc": huc12}
    if basin is not None:
        values["basin"] = basin
    session.execute(update(Reach).where(Reach.id == reach_id).values(**values))


def get_reach_huc_counts(session: Session) -> dict[int, int]:
    """Return ``{length: count}`` for ``reach.huc`` values in the DB.

    NULL/empty rows fall under length 0. Useful for quick before/after
    snapshots: a healthy DB has every row at length 12.
    """
    rows = session.execute(
        select(func.length(Reach.huc), func.count())
        .group_by(func.length(Reach.huc))
        .order_by(func.length(Reach.huc))
    ).all()
    return {int(length or 0): int(count) for length, count in rows}


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
