"""Pipeline orchestrator (replaces scripts/master).

Runs the full data pipeline in order:
1. fetch — fetch from remote agencies
2. calc-rating — apply rating tables
3. merge — merge multi-source data
4. calculator — compute derived values
5. build — generate output pages
"""

from __future__ import annotations

import sys
import time

from kayak.cli import build, calc_rating, calculator, fetch, merge


def addArgs(subparsers):
    """Register the 'pipeline' subcommand."""
    parser = subparsers.add_parser("pipeline",
                                   help="Run the full data pipeline")
    parser.set_defaults(func=pipeline)
    parser.add_argument("--skip-fetch", action="store_true", help="Skip the fetch step")
    parser.add_argument("-d", "--dry-run", action="store_true", help="Dry run (no DB writes)")


def pipeline(args):
    """Run the full data pipeline (fetch -> calc-rating -> merge -> calculator -> build)."""
    steps = []

    if not args.skip_fetch:
        steps.append(("fetch", fetch.fetch))

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
            print(f"Error in {step_name}: {e}", file=sys.stderr)
        elapsed = time.time() - start
        print(f"Completed {step_name} in {elapsed:.1f}s")

    print(f"\n{'='*60}")
    print("Pipeline complete")
