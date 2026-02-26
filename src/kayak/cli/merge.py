"""Merger command (replaces merger.C).

Combines data from multiple source stations into merged gauges via
the GaugeSource relationships.
"""

from __future__ import annotations

import logging

import click

from kayak.db.data_db import merge_sources, update_latest
from kayak.db.engine import get_session
from kayak.db.models import DataType, Gauge, GaugeSource, Source

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
        # Find gauges that have multiple sources (candidates for merging)
        gauges = session.query(Gauge).all()

        types = [DataType.flow, DataType.inflow, DataType.gauge, DataType.temperature]

        merge_count = 0
        for gauge in gauges:
            source_ids = [
                gs.source_id
                for gs in session.query(GaugeSource)
                .filter(GaugeSource.gauge_id == gauge.id)
                .all()
            ]

            if len(source_ids) < 2:
                continue

            # Use the first source as the merge target
            target_id = source_ids[0]
            input_ids = source_ids[1:]

            for dtype in types:
                try:
                    count = merge_sources(session, target_id, input_ids, dtype)
                    if verbose and count > 0:
                        click.echo(f"  {gauge.name}/{dtype.value}: {count} rows merged")
                    if count > 0:
                        update_latest(session, target_id, dtype)
                        merge_count += count
                except Exception as e:
                    click.echo(
                        f"  Error merging {gauge.name}/{dtype.value}: {e}",
                        err=True,
                    )

        click.echo(f"Found {merge_count} observations merged")
        session.commit()
        click.echo("Merge complete")
    finally:
        session.close()
