"""Rating table calculator (replaces calcRating.C).

Applies gage height <-> flow conversions via interpolated rating tables.
If only gage data exists, converts to flow. If only flow, converts to gage.
If both exist, fills in gaps.
"""

from __future__ import annotations

import logging

import click

from kayak.db.data_db import (
    get_observations,
    get_rating_table,
    store_observation,
    update_latest,
)
from kayak.db.engine import get_session
from kayak.db.models import DataType, Gauge, GaugeSource, Source
from kayak.utils.conversions import interpolate_rating

logger = logging.getLogger(__name__)


@click.command("calc-rating")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output")
def calc_rating_cmd(verbose):
    """Apply rating tables to convert between gage height and flow."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

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

        click.echo(f"Found {len(gauges)} gauges with rating tables")

        for gauge in gauges:
            try:
                # Load rating table (feet -> cfs)
                feet_to_cfs = get_rating_table(session, gauge.rating_id)
                if not feet_to_cfs or len(feet_to_cfs) < 2:
                    if verbose:
                        click.echo(f"  {gauge.name}: no rating table entries")
                    continue

                # Build reverse table (cfs -> feet)
                cfs_to_feet = sorted(
                    [(cfs, feet) for feet, cfs in feet_to_cfs],
                    key=lambda x: x[0],
                )

                # Get source IDs for this gauge
                source_ids = [
                    gs.source_id
                    for gs in session.query(GaugeSource)
                    .filter(GaugeSource.gauge_id == gauge.id)
                    .all()
                ]

                for source_id in source_ids:
                    gauge_records = get_observations(session, source_id, DataType.gauge)
                    flow_records = get_observations(session, source_id, DataType.flow)

                    if verbose:
                        click.echo(
                            f"  {gauge.name} src={source_id}: {len(feet_to_cfs)} rating entries, "
                            f"{len(gauge_records)} gauge, {len(flow_records)} flow"
                        )

                    new_gauge = False
                    new_flow = False

                    if not gauge_records:
                        for rec in flow_records:
                            val = interpolate_rating(cfs_to_feet, rec.value, 0.1)
                            if val is not None:
                                if store_observation(
                                    session, source_id, DataType.gauge, rec.observed_at, val
                                ):
                                    new_gauge = True
                    elif not flow_records:
                        for rec in gauge_records:
                            val = interpolate_rating(feet_to_cfs, rec.value, 1.0)
                            if val is not None and val > 0:
                                if store_observation(
                                    session, source_id, DataType.flow, rec.observed_at, val
                                ):
                                    new_flow = True
                    else:
                        flow_times = {rec.observed_at for rec in flow_records}
                        gauge_times = {rec.observed_at for rec in gauge_records}

                        for rec in flow_records:
                            if rec.observed_at not in gauge_times:
                                val = interpolate_rating(cfs_to_feet, rec.value, 0.1)
                                if val is not None:
                                    if store_observation(
                                        session, source_id, DataType.gauge,
                                        rec.observed_at, val,
                                    ):
                                        new_gauge = True

                        for rec in gauge_records:
                            if rec.observed_at not in flow_times:
                                val = interpolate_rating(feet_to_cfs, rec.value, 1.0)
                                if val is not None and val > 0:
                                    if store_observation(
                                        session, source_id, DataType.flow,
                                        rec.observed_at, val,
                                    ):
                                        new_flow = True

                    if new_flow:
                        update_latest(session, source_id, DataType.flow)
                    if new_gauge:
                        update_latest(session, source_id, DataType.gauge)

            except Exception as e:
                click.echo(f"  Error for {gauge.name}: {e}", err=True)

        session.commit()
        click.echo("Rating calculations complete")
    finally:
        session.close()
