"""Source lookups and source-level filters."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from kayak.db.models import Gauge, GaugeSource, Source


def get_source_by_name(session: Session, name: str) -> Source | None:
    """Fetch a Source by its name, or None.

    Source.name is not unique — the same physical station may have multiple
    source rows (e.g., WA DOE publishes separate URLs per data type; `NMFO3`
    is published by both NWS and nwps). Callers needing a specific row should
    disambiguate by agency or fetch_url_id.
    """
    return session.execute(select(Source).where(Source.name == name)).scalars().first()


def is_source_calculated(session: Session, source_id: int) -> bool:
    """Return True if the source uses a calc_expression instead of a fetch URL."""
    src = session.get(Source, source_id)
    return src is not None and src.calc_expression_id is not None


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


def get_negative_flow_source_ids(session: Session) -> set[int]:
    """Return source_ids linked to gauges with allow_negative_flow=True."""
    rows = session.execute(
        select(GaugeSource.source_id)
        .join(Gauge, GaugeSource.gauge_id == Gauge.id)
        .where(Gauge.allow_negative_flow.is_(True))
    ).all()
    return {r[0] for r in rows}
