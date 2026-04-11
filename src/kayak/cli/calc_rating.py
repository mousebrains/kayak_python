"""Rating table calculator (replaces calcRating.C).

Applies gage height <-> flow conversions via interpolated rating tables.
If only gage data exists, converts to flow. If only flow, converts to gage.
If both exist, fills in gaps.
"""

from __future__ import annotations

import argparse
import logging

from kayak.db.data_db import (
    get_observations,
    get_rating_table,
    store_observation,
    update_latest,
)
from kayak.db.engine import get_session
from kayak.db.info_db import get_source_ids_for_gauge
from kayak.db.models import DataType, Gauge
from kayak.utils.conversions import interpolate_rating

logger = logging.getLogger(__name__)


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the 'calc-rating' subcommand."""
    parser = subparsers.add_parser("calc-rating",
                                   help="Apply rating tables to convert between gage height and flow")
    parser.set_defaults(func=calc_rating)


def calc_rating(args: argparse.Namespace) -> None:
    """Apply rating tables to convert between gage height and flow."""

    session = get_session()
    try:
        # Find gauges with rating tables
        gauges = (
            session.query(Gauge)
            .filter(
                Gauge.rating_id.isnot(None),
            )
            .all()
        )

        print(f"Found {len(gauges)} gauges with rating tables")

        for gauge in gauges:
            try:
                if gauge.rating_id is None:
                    continue
                # Load rating table (feet -> cfs)
                feet_to_cfs = get_rating_table(session, gauge.rating_id)
                if not feet_to_cfs or len(feet_to_cfs) < 2:
                    logger.debug("%s: no rating table entries", gauge.name)
                    continue

                # Build reverse table (cfs -> feet)
                cfs_to_feet = sorted(
                    [(cfs, feet) for feet, cfs in feet_to_cfs],
                    key=lambda x: x[0],
                )

                source_ids = get_source_ids_for_gauge(session, gauge.id)

                for source_id in source_ids:
                    gauge_records = get_observations(session, source_id, DataType.gauge)
                    flow_records = get_observations(session, source_id, DataType.flow)

                    logger.info(
                        "%s src=%s: %d rating entries, %d gauge, %d flow",
                        gauge.name, source_id, len(feet_to_cfs),
                        len(gauge_records), len(flow_records),
                    )

                    new_gauge = False
                    new_flow = False

                    if not gauge_records:
                        for rec in flow_records:
                            val = interpolate_rating(cfs_to_feet, rec.value, 0.1)
                            if val is not None and store_observation(
                                session, source_id, DataType.gauge, rec.observed_at, val
                            ):
                                new_gauge = True
                    elif not flow_records:
                        for rec in gauge_records:
                            val = interpolate_rating(feet_to_cfs, rec.value, 1.0)
                            if (
                                val is not None
                                and val > 0
                                and store_observation(
                                    session, source_id, DataType.flow, rec.observed_at, val
                                )
                            ):
                                new_flow = True
                    else:
                        flow_times = {rec.observed_at for rec in flow_records}
                        gauge_times = {rec.observed_at for rec in gauge_records}

                        for rec in flow_records:
                            if rec.observed_at not in gauge_times:
                                val = interpolate_rating(cfs_to_feet, rec.value, 0.1)
                                if val is not None and store_observation(
                                    session, source_id, DataType.gauge,
                                    rec.observed_at, val,
                                ):
                                    new_gauge = True

                        for rec in gauge_records:
                            if rec.observed_at not in flow_times:
                                val = interpolate_rating(feet_to_cfs, rec.value, 1.0)
                                if (
                                    val is not None
                                    and val > 0
                                    and store_observation(
                                        session, source_id, DataType.flow,
                                        rec.observed_at, val,
                                    )
                                ):
                                    new_flow = True

                    if new_flow:
                        update_latest(session, source_id, DataType.flow)
                    if new_gauge:
                        update_latest(session, source_id, DataType.gauge)

                # Commit after each gauge to release the write lock
                session.commit()

            except Exception as e:
                session.rollback()
                logger.error("Error for %s: %s", gauge.name, e)

        print("Rating calculations complete")
    finally:
        session.close()
