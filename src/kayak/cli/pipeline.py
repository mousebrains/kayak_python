"""Pipeline orchestrator (replaces scripts/master).

Runs the full data pipeline in order:
1. fetch — fetch from remote agencies
2. fetch-usgs-ogc — fetch USGS data via OGC API
3. calc-rating — apply rating tables
4. update-gauge-cache — recompute gauge-level latest values
5. calculator — compute derived values
6. build — generate output pages
"""

from __future__ import annotations

import argparse
import logging
import time

from sqlalchemy import text

from kayak.cli import build, calc_rating, calculator, fetch, fetch_usgs_ogc
from kayak.db.engine import get_engine

logger = logging.getLogger(__name__)


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the 'pipeline' subcommand."""
    parser = subparsers.add_parser("pipeline", help="Run the full data pipeline")
    parser.set_defaults(func=pipeline)
    parser.add_argument("--skip-fetch", action="store_true", help="Skip the fetch step")

    # Include all fetch options (dry-run, input-dir, etc.)
    fetch.addArgs_options(parser)


def _update_gauge_cache(args: argparse.Namespace) -> None:
    """Recompute gauge-level latest observation cache."""
    from kayak.db.data_db import update_all_latest_gauges
    from kayak.db.engine import get_session

    session = get_session()
    try:
        update_all_latest_gauges(session)
        print("Gauge cache updated")
    finally:
        session.close()


def pipeline(args: argparse.Namespace) -> None:
    """Run the full data pipeline."""
    steps = []

    if not args.skip_fetch:
        steps.append(("fetch", fetch.fetch))

    steps.append(("fetch-usgs-ogc", fetch_usgs_ogc.fetch_usgs_ogc))

    steps.extend(
        [
            ("calc-rating", calc_rating.calc_rating),
            ("update-gauge-cache", _update_gauge_cache),
            ("calculator", calculator.calculator),
            ("build", build.build),
        ]
    )

    for step_name, func in steps:
        print(f"\n{'=' * 60}", flush=True)
        print(f"Running: {step_name}", flush=True)
        print(f"{'=' * 60}", flush=True)
        start = time.time()
        try:
            func(args)
        except SystemExit:
            pass
        except Exception as e:
            logger.error("Error in %s: %s", step_name, e)
        elapsed = time.time() - start
        print(f"Completed {step_name} in {elapsed:.1f}s", flush=True)

    # Run PRAGMA optimize to update SQLite query planner statistics
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("PRAGMA optimize"))
            conn.commit()
    except Exception as e:
        logger.warning("PRAGMA optimize failed: %s", e)

    print(f"\n{'=' * 60}", flush=True)
    print("Pipeline complete", flush=True)
