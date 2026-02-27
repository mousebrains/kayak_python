"""Section/Gauge/State query helpers (replaces InfoDB.C).

Uses the normalized schema with Section, Gauge, State, and junction tables
instead of the flat Master/MergedMaster approach.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from kayak.db.models import Gauge, GaugeSource, Section, State


def all_states(session: Session) -> list[State]:
    """Return all State records sorted by name."""
    return list(session.scalars(
        select(State).order_by(State.name)
    ))


def all_state_names(session: Session) -> list[str]:
    """Return sorted list of distinct state names."""
    rows = session.execute(
        select(State.name).order_by(State.name)
    ).scalars().all()
    return list(rows)


def sections_query(
    session: Session,
    *,
    state_name: str | None = None,
    visible_only: bool = True,
    with_gauge: bool = False,
) -> list[Section]:
    """Query sections with optional filtering.

    Args:
        state_name: Filter by state name if provided
        visible_only: Exclude no_show sections (default True)
        with_gauge: Eagerly load gauge relationship
    """
    stmt = select(Section).order_by(Section.sort_name)

    if visible_only:
        stmt = stmt.where(Section.no_show.is_(False))

    if with_gauge:
        stmt = stmt.options(joinedload(Section.gauge))

    if state_name:
        stmt = stmt.join(Section.states).where(State.name == state_name)

    return list(session.scalars(stmt))


def get_section(session: Session, section_id: int) -> Section | None:
    """Fetch a Section by ID."""
    return session.get(Section, section_id)


def get_section_by_name(session: Session, name: str) -> Section | None:
    """Fetch a Section by its unique name."""
    return session.execute(
        select(Section).where(Section.name == name)
    ).scalar_one_or_none()


def display_name(session: Session, section_id: int) -> str | None:
    """Get display_name for a section ID."""
    row = session.get(Section, section_id)
    return row.display_name if row else None


def get_gauge_for_section(session: Session, section_id: int) -> Gauge | None:
    """Get the Gauge associated with a Section."""
    section = session.get(Section, section_id)
    if section is None or section.gauge_id is None:
        return None
    return session.get(Gauge, section.gauge_id)


def get_primary_source_id(session: Session, gauge_id: int) -> int | None:
    """Return the source_id of the first GaugeSource for a gauge, or None."""
    gs = session.execute(
        select(GaugeSource.source_id).where(GaugeSource.gauge_id == gauge_id)
    ).scalar()
    return gs


def get_source_ids_for_gauge(session: Session, gauge_id: int) -> list[int]:
    """Return all source_ids linked to a gauge."""
    rows = session.execute(
        select(GaugeSource.source_id).where(GaugeSource.gauge_id == gauge_id)
    ).scalars().all()
    return list(rows)
