"""Merger command (replaces merger.C).

Combines data from multiple source stations into merged gauges via
the GaugeSource relationships.
"""

from __future__ import annotations

import logging

from kayak.db.data_db import merge_sources, update_latest
from kayak.db.engine import get_session
from kayak.db.info_db import get_source_ids_for_gauge
from kayak.db.models import DataType, Gauge

logger = logging.getLogger(__name__)


def addArgs(subparsers):
    """Register the 'merge' subcommand."""
    parser = subparsers.add_parser("merge",
                                   help="Merge data from multiple source stations into combined stations")
    parser.set_defaults(func=merge)


def merge(args):
    """Merge data from multiple source stations into combined stations."""

    session = get_session()
    try:
        # Find gauges that have multiple sources (candidates for merging)
        gauges = session.query(Gauge).all()

        types = [DataType.flow, DataType.inflow, DataType.gauge, DataType.temperature]

        merge_count = 0
        for gauge in gauges:
            source_ids = get_source_ids_for_gauge(session, gauge.id)

            if len(source_ids) < 2:
                continue

            # Use the first source as the merge target
            target_id = source_ids[0]
            input_ids = source_ids[1:]

            for dtype in types:
                try:
                    count = merge_sources(session, target_id, input_ids, dtype)
                    if count > 0:
                        logger.info("%s/%s: %d rows merged", gauge.name, dtype.value, count)
                    if count > 0:
                        update_latest(session, target_id, dtype)
                        merge_count += count
                except Exception as e:
                    logger.error("Error merging %s/%s: %s", gauge.name, dtype.value, e)

        print(f"Found {merge_count} observations merged")
        session.commit()
        print("Merge complete")
    finally:
        session.close()
