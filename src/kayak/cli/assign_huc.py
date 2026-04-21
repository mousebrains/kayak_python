"""``levels assign-huc`` — backfill ``reach.huc`` with HUC12 codes.

Thin CLI wrapper around :mod:`kayak.huc.assign`. The heavy spatial imports
(geopandas, shapely) are deferred into the entry function so ``levels --help``
loads fast on hosts that don't have the geo extras installed.
"""

from __future__ import annotations

import argparse
import sys


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the 'assign-huc' subcommand."""
    parser = subparsers.add_parser(
        "assign-huc",
        help="Assign HUC12 watershed codes to reaches via put-in coordinates",
    )
    parser.set_defaults(func=assign_huc)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report assignments without writing to the database",
    )
    parser.add_argument(
        "--reach-id",
        type=int,
        default=None,
        help="Only assign one reach (default: all reaches with put-in coords)",
    )
    parser.add_argument(
        "--gpkg",
        default="Trace-cache/wbd.gpkg",
        help="Path to extracted WBD GeoPackage (built by scripts/extract_wbd.sh)",
    )


def assign_huc(args: argparse.Namespace) -> None:
    """Entry point for ``levels assign-huc``.

    Imports :mod:`kayak.huc.assign` lazily so the geopandas/shapely
    dependency is only loaded when this command actually runs.
    """
    try:
        from kayak.huc import assign as impl
    except ImportError as exc:
        print(
            f"error: cannot load HUC module — install with `pip install -e .[geo]`: {exc}",
            file=sys.stderr,
        )
        sys.exit(2)

    impl.run(gpkg=args.gpkg, reach_id=args.reach_id, dry_run=args.dry_run)
