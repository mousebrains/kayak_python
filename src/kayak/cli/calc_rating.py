"""Rating table calculator (replaces calcRating.C).

Applies gage height <-> flow conversions via interpolated rating tables.
If only gage data exists, converts to flow. If only flow, converts to gage.
If both exist, fills in gaps.
"""

from __future__ import annotations

import logging

import click

from kayak.db.data_db import (
    get_measurements,
    get_rating_table,
    store_measurement,
    update_latest,
)
from kayak.db.engine import get_session
from kayak.db.models import DataType, MergedMaster
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
        # Find stations with rating table data
        records = (
            session.query(MergedMaster)
            .filter(
                MergedMaster.db_name.isnot(None),
                MergedMaster.db_name != "",
                MergedMaster.cfs_to_gauge_data.isnot(None),
                MergedMaster.cfs_to_gauge_data != "",
            )
            .all()
        )

        click.echo(f"Found {len(records)} stations with rating tables")

        for record in records:
            db_name = record.db_name
            rating_name = record.cfs_to_gauge_data

            try:
                # Load rating table (feet -> cfs)
                feet_to_cfs = get_rating_table(session, rating_name)
                if not feet_to_cfs or len(feet_to_cfs) < 2:
                    if verbose:
                        click.echo(f"  {db_name}: no rating table entries")
                    continue

                # Build reverse table (cfs -> feet)
                cfs_to_feet = sorted(
                    [(cfs, feet) for feet, cfs in feet_to_cfs],
                    key=lambda x: x[0],
                )

                # Get existing measurements
                feet_records = get_measurements(session, db_name, DataType.GAGE)
                cfs_records = get_measurements(session, db_name, DataType.FLOW)

                if verbose:
                    click.echo(
                        f"  {db_name}: {len(feet_to_cfs)} rating entries, "
                        f"{len(feet_records)} gage, {len(cfs_records)} flow"
                    )

                new_feet = False
                new_cfs = False

                if not feet_records:
                    # Convert all CFS to feet
                    for rec in cfs_records:
                        val = interpolate_rating(cfs_to_feet, rec.value, 0.1)
                        if val is not None:
                            if store_measurement(
                                session, db_name, DataType.GAGE, rec.time, val
                            ):
                                new_feet = True
                elif not cfs_records:
                    # Convert all feet to CFS
                    for rec in feet_records:
                        val = interpolate_rating(feet_to_cfs, rec.value, 1.0)
                        if val is not None and val > 0:
                            if store_measurement(
                                session, db_name, DataType.FLOW, rec.time, val
                            ):
                                new_cfs = True
                else:
                    # Both exist — fill gaps
                    cfs_times = {rec.time for rec in cfs_records}
                    feet_times = {rec.time for rec in feet_records}

                    for rec in cfs_records:
                        if rec.time not in feet_times:
                            val = interpolate_rating(cfs_to_feet, rec.value, 0.1)
                            if val is not None:
                                if store_measurement(
                                    session, db_name, DataType.GAGE,
                                    rec.time, val,
                                ):
                                    new_feet = True

                    for rec in feet_records:
                        if rec.time not in cfs_times:
                            val = interpolate_rating(feet_to_cfs, rec.value, 1.0)
                            if val is not None and val > 0:
                                if store_measurement(
                                    session, db_name, DataType.FLOW,
                                    rec.time, val,
                                ):
                                    new_cfs = True

                if new_cfs:
                    update_latest(session, db_name, DataType.FLOW)
                if new_feet:
                    update_latest(session, db_name, DataType.GAGE)

            except Exception as e:
                click.echo(f"  Error for {db_name}: {e}", err=True)

        session.commit()
        click.echo("Rating calculations complete")
    finally:
        session.close()
