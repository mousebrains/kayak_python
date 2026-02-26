"""Merger command (replaces merger.C).

Combines data from multiple source stations into merged tables.
"""

from __future__ import annotations

import logging

import click

from kayak.db.data_db import merge_stations, update_latest
from kayak.db.engine import get_session
from kayak.db.models import DataType, MergedMaster

logger = logging.getLogger(__name__)


@click.command("merge")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output")
def merge_cmd(verbose):
    """Merge data from multiple source stations into combined stations."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    session = get_session()
    try:
        # Query records that have both merged_dbs and db_name
        records = (
            session.query(MergedMaster)
            .filter(
                MergedMaster.merged_dbs.isnot(None),
                MergedMaster.db_name.isnot(None),
            )
            .all()
        )

        click.echo(f"Found {len(records)} stations to merge")

        types = [
            DataType.FLOW, DataType.INFLOW, DataType.OUTFLOW,
            DataType.GAGE, DataType.TEMPERATURE,
        ]

        for record in records:
            db_name = record.db_name
            source_dbs = record.merged_dbs.split()

            for dtype in types:
                try:
                    count = merge_stations(session, db_name, source_dbs, dtype)
                    if verbose and count > 0:
                        click.echo(f"  {db_name}/{dtype.value}: {count} rows merged")
                except Exception as e:
                    click.echo(
                        f"  Error merging {db_name}/{dtype.value}: {e}",
                        err=True,
                    )

        session.commit()
        click.echo("Merge complete")
    finally:
        session.close()
