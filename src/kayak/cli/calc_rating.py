"""Rating table calculator.

Applies gage height <-> flow conversions via interpolated rating tables.
If only gage data exists, converts to flow. If only flow, converts to gage.
If both exist, fills in gaps.
"""

import argparse
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from kayak.db.cache import update_latest, update_latest_gauge
from kayak.db.engine import get_session
from kayak.db.gauges import get_source_ids_for_gauge
from kayak.db.models import DataType, Gauge
from kayak.db.observations import get_observations, get_rating_table, store_observation
from kayak.db.sources import get_negative_flow_source_ids
from kayak.utils.conversions import interpolate_rating

logger = logging.getLogger(__name__)


def addArgs(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the 'calc-rating' subcommand."""
    parser = subparsers.add_parser(
        "calc-rating", help="Apply rating tables to convert between gage height and flow"
    )
    parser.set_defaults(func=calc_rating)


def calc_rating(args: argparse.Namespace) -> None:
    """Apply rating tables to convert between gage height and flow."""

    session = get_session()
    try:
        gauges = list(session.scalars(select(Gauge).where(Gauge.rating_id.isnot(None))))
        neg_flow_sources = get_negative_flow_source_ids(session)

        print(f"Found {len(gauges)} gauges with rating tables")

        for gauge in gauges:
            try:
                if gauge.rating_id is None:
                    continue
                tables = _load_rating_for_gauge(session, gauge.rating_id, gauge.name)
                if tables is None:
                    continue
                feet_to_cfs, cfs_to_feet = tables

                source_ids = get_source_ids_for_gauge(session, gauge.id)
                any_new_gauge = False
                any_new_flow = False

                for source_id in source_ids:
                    new_gauge, new_flow = _apply_rating_to_source(
                        session,
                        source_id,
                        gauge_name=gauge.name,
                        feet_to_cfs=feet_to_cfs,
                        cfs_to_feet=cfs_to_feet,
                        neg_flow_sources=neg_flow_sources,
                    )
                    any_new_gauge = any_new_gauge or new_gauge
                    any_new_flow = any_new_flow or new_flow

                if any_new_flow:
                    update_latest_gauge(session, gauge.id, DataType.flow)
                if any_new_gauge:
                    update_latest_gauge(session, gauge.id, DataType.gauge)

                # Commit after each gauge to release the SQLite writer lock.
                session.commit()

            except Exception as e:
                session.rollback()
                logger.error("Error for %s: %s", gauge.name, e)

        print("Rating calculations complete")
    finally:
        session.close()


def _load_rating_for_gauge(
    session: Session, rating_id: int, gauge_name: str
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]] | None:
    """Load the (feet→cfs, cfs→feet) tables for a rating, or None if unusable.

    The cfs→feet table is sorted ascending by cfs so interpolate_rating can
    walk it the same way as the forward table; the (cfs, feet) tuple shape
    matters because interpolate_rating expects (x, y) pairs.
    """
    feet_to_cfs = get_rating_table(session, rating_id)
    if not feet_to_cfs or len(feet_to_cfs) < 2:
        logger.debug("%s: no rating table entries", gauge_name)
        return None
    cfs_to_feet = sorted(
        [(cfs, feet) for feet, cfs in feet_to_cfs],
        key=lambda x: x[0],
    )
    return feet_to_cfs, cfs_to_feet


def _apply_rating_to_source(
    session: Session,
    source_id: int,
    *,
    gauge_name: str,
    feet_to_cfs: list[tuple[float, float]],
    cfs_to_feet: list[tuple[float, float]],
    neg_flow_sources: set[int],
) -> tuple[bool, bool]:
    """Cross-fill missing gauge/flow observations for one source.

    Returns ``(new_gauge, new_flow)`` indicating which side received
    at least one new row (so the caller can refresh the per-source
    latest cache). The two fill helpers use the pre-loop snapshot of
    ``gauge_times`` / ``flow_times`` — newly-stored rows do NOT feed
    back into the in-loop time set. This invariant is pinned by
    ``test_both_exist_uses_pre_loop_time_sets``.
    """
    gauge_records = get_observations(session, source_id, DataType.gauge)
    flow_records = get_observations(session, source_id, DataType.flow)

    logger.info(
        "%s src=%s: %d rating entries, %d gauge, %d flow",
        gauge_name,
        source_id,
        len(feet_to_cfs),
        len(gauge_records),
        len(flow_records),
    )

    gauge_times = {rec.observed_at for rec in gauge_records}
    flow_times = {rec.observed_at for rec in flow_records}

    new_gauge = _fill_gauge_from_flow(
        session, source_id, flow_records, gauge_times, cfs_to_feet, neg_flow_sources
    )
    new_flow = _fill_flow_from_gauge(
        session, source_id, gauge_records, flow_times, feet_to_cfs, neg_flow_sources
    )

    if new_flow:
        update_latest(session, source_id, DataType.flow)
    if new_gauge:
        update_latest(session, source_id, DataType.gauge)

    return new_gauge, new_flow


def _fill_gauge_from_flow(
    session: Session,
    source_id: int,
    flow_records: list,
    gauge_times: set,
    cfs_to_feet: list[tuple[float, float]],
    neg_flow_sources: set[int],
) -> bool:
    """For each flow record whose timestamp has no gauge row yet, derive one.

    No ``val > 0`` guard — gauge readings can legitimately be zero.
    """
    new_gauge = False
    for rec in flow_records:
        if rec.observed_at in gauge_times:
            continue
        val = interpolate_rating(cfs_to_feet, rec.value, 0.1)
        if val is not None and store_observation(
            session,
            source_id,
            DataType.gauge,
            rec.observed_at,
            val,
            allow_negative_flow_sources=neg_flow_sources,
        ):
            new_gauge = True
    return new_gauge


def _fill_flow_from_gauge(
    session: Session,
    source_id: int,
    gauge_records: list,
    flow_times: set,
    feet_to_cfs: list[tuple[float, float]],
    neg_flow_sources: set[int],
) -> bool:
    """For each gauge record whose timestamp has no flow row yet, derive one.

    The ``val > 0`` guard is intentional — zero or negative flow from a
    rating-table interpolation is treated as out-of-domain. Pinned by
    ``test_zero_flow_value_not_stored``.
    """
    new_flow = False
    for rec in gauge_records:
        if rec.observed_at in flow_times:
            continue
        val = interpolate_rating(feet_to_cfs, rec.value, 1.0)
        if (
            val is not None
            and val > 0
            and store_observation(
                session,
                source_id,
                DataType.flow,
                rec.observed_at,
                val,
                allow_negative_flow_sources=neg_flow_sources,
            )
        ):
            new_flow = True
    return new_flow
