"""Argparse CLI entry point (replaces individual C++ programs)."""

import argparse
import sys

from kayak.cli import (
    assign_huc,
    build,
    calc_rating,
    calculator,
    decimate,
    delete_editor,
    editor_retention,
    emit_config,
    export_editor,
    fetch,
    fetch_usgs_ogc,
    init_db,
    merge,
    migrate,
    orphan_check,
    pipeline,
    seed_maintainer,
    trace_reach,
    validate_config,
)
from kayak.cli.logger import addArgs as addLoggerArgs
from kayak.cli.logger import mkLogger


def main() -> None:
    """levels - River level data aggregation from government agencies."""
    parser = argparse.ArgumentParser(
        prog="levels",
        description="River level data aggregation from government agencies",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")

    addLoggerArgs(parser)

    subparsers = parser.add_subparsers(dest="command")

    init_db.addArgs(subparsers)
    migrate.addArgs(subparsers)
    fetch.addArgs(subparsers)
    fetch_usgs_ogc.addArgs(subparsers)
    merge.addArgs(subparsers)
    calc_rating.addArgs(subparsers)
    calculator.addArgs(subparsers)
    build.addArgs(subparsers)
    decimate.addArgs(subparsers)
    orphan_check.addArgs(subparsers)
    pipeline.addArgs(subparsers)
    seed_maintainer.addArgs(subparsers)
    delete_editor.addArgs(subparsers)
    export_editor.addArgs(subparsers)
    editor_retention.addArgs(subparsers)
    trace_reach.addArgs(subparsers)
    assign_huc.addArgs(subparsers)
    emit_config.addArgs(subparsers)
    validate_config.addArgs(subparsers)

    args = parser.parse_args()
    mkLogger(args)

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)
