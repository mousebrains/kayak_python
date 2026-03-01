"""Pipeline orchestrator (replaces scripts/master).

Runs the full data pipeline in order:
1. fetch — fetch from remote agencies
2. calc-rating — apply rating tables
3. merge — merge multi-source data
4. calculator — compute derived values
5. build — generate output pages
"""

from __future__ import annotations

import logging
import os
import time

from sqlalchemy import text

from kayak.cli import build, calc_rating, calculator, fetch, fetch_usgs_ogc, merge
from kayak.db.engine import get_engine

logger = logging.getLogger(__name__)


def addArgs(subparsers):
    """Register the 'pipeline' subcommand."""
    parser = subparsers.add_parser("pipeline",
                                   help="Run the full data pipeline")
    parser.set_defaults(func=pipeline)
    parser.add_argument("--skip-fetch", action="store_true", help="Skip the fetch step")

    # Include all fetch options (dry-run, input-dir, etc.)
    fetch.addArgs_options(parser)


def pipeline(args):
    """Run the full data pipeline (fetch -> calc-rating -> merge -> calculator -> build)."""
    steps = []

    if not args.skip_fetch:
        steps.append(("fetch", fetch.fetch))

    if os.environ.get("USGS_API_KEY"):
        steps.append(("fetch-usgs-ogc", fetch_usgs_ogc.fetch_usgs_ogc))

    steps.extend([
        ("calc-rating", calc_rating.calc_rating),
        ("merge", merge.merge),
        ("calculator", calculator.calculator),
        ("build", build.build),
    ])

    for step_name, func in steps:
        print(f"\n{'='*60}")
        print(f"Running: {step_name}")
        print(f"{'='*60}")
        start = time.time()
        try:
            func(args)
        except SystemExit:
            pass
        except Exception as e:
            logger.error("Error in %s: %s", step_name, e)
        elapsed = time.time() - start
        print(f"Completed {step_name} in {elapsed:.1f}s")

    # Run PRAGMA optimize to update SQLite query planner statistics
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("PRAGMA optimize"))
            conn.commit()
    except Exception as e:
        logger.warning("PRAGMA optimize failed: %s", e)

    print(f"\n{'='*60}")
    print("Pipeline complete")
