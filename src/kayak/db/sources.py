"""Source lookups and source-level filters."""

from datetime import datetime
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kayak.db.models import FetchUrl, Gauge, GaugeSource, LatestObservation, Source


class OrphanRow(NamedTuple):
    """A fetch-active source row with no gauge_source link.

    Returned by :func:`find_orphan_sources`. ``latest_obs`` is the
    most-recent ``observed_at`` across all data_types for this source
    in ``latest_observation`` (NULL if the source has never received
    an observation).
    """

    source_id: int
    name: str
    agency: str | None
    url: str
    is_active: bool
    latest_obs: datetime | None


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


def find_orphan_sources(session: Session) -> list[OrphanRow]:
    """Return fetch-backed source rows with no gauge_source link.

    Filters to "still relevant" cases: either the fetch_url is active,
    or the source has received an observation in the last 7 days. The
    7-day grace lets a freshly-deactivated URL drop off the list once
    its data ages out, while still flagging "URL got deactivated but
    cleanup wasn't finished" between fetch runs.

    Used by :func:`kayak.cli.orphan_check.orphan_check` and by the
    pipeline's end-of-run gate (Phase 2b of
    ``docs/PLAN_orphan_sources.md``).

    ``MAX(latest_obs)`` aggregates across the per-data_type rows in
    ``latest_observation`` — a source emitting multiple data_types
    contributes one row per type pre-GROUP BY; we want the freshest.
    """
    stmt = (
        select(
            Source.id,
            Source.name,
            Source.agency,
            FetchUrl.url,
            FetchUrl.is_active,
            func.max(LatestObservation.observed_at).label("latest_obs"),
        )
        .outerjoin(GaugeSource, GaugeSource.source_id == Source.id)
        .outerjoin(FetchUrl, FetchUrl.id == Source.fetch_url_id)
        .outerjoin(LatestObservation, LatestObservation.source_id == Source.id)
        .where(GaugeSource.source_id.is_(None))
        .where(Source.fetch_url_id.is_not(None))
        .where(
            (FetchUrl.is_active.is_(True))
            | (LatestObservation.observed_at > func.datetime("now", "-7 days"))
        )
        .group_by(Source.id)
    )
    return [
        OrphanRow(
            source_id=row.id,
            name=row.name,
            agency=row.agency,
            url=row.url,
            is_active=bool(row.is_active),
            latest_obs=row.latest_obs,
        )
        for row in session.execute(stmt)
    ]
