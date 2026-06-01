"""Argparse CLI entry point (replaces individual C++ programs)."""

import argparse
import sys

from kayak import __version__
from kayak.cli import (
    analyze_logs,
    assign_huc,
    build,
    calc_rating,
    calculator,
    check_reaches,
    decimate,
    delete_editor,
    editor_retention,
    emit_config,
    export_editor,
    fetch,
    fetch_osmb,
    fetch_usgs_ogc,
    init_db,
    migrate,
    orphan_check,
    pipeline,
    seed_maintainer,
    status,
    sync_metadata,
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
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    addLoggerArgs(parser)

    subparsers = parser.add_subparsers(dest="command")

    init_db.addArgs(subparsers)
    migrate.addArgs(subparsers)
    fetch.addArgs(subparsers)
    fetch_osmb.addArgs(subparsers)
    fetch_usgs_ogc.addArgs(subparsers)
    calc_rating.addArgs(subparsers)
    calculator.addArgs(subparsers)
    build.addArgs(subparsers)
    decimate.addArgs(subparsers)
    orphan_check.addArgs(subparsers)
    check_reaches.addArgs(subparsers)
    pipeline.addArgs(subparsers)
    seed_maintainer.addArgs(subparsers)
    delete_editor.addArgs(subparsers)
    export_editor.addArgs(subparsers)
    editor_retention.addArgs(subparsers)
    trace_reach.addArgs(subparsers)
    assign_huc.addArgs(subparsers)
    emit_config.addArgs(subparsers)
    validate_config.addArgs(subparsers)
    analyze_logs.addArgs(subparsers)
    status.addArgs(subparsers)
    sync_metadata.addArgs(subparsers)

    args = parser.parse_args()
    mkLogger(args)

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    # Handlers either return None (success → exit 0) or an int exit code
    # (e.g. check-reaches returns 1 when it flags issues). Map the latter
    # onto sys.exit; handlers that need other codes still call sys.exit
    # themselves before returning. Exclude bool (a subclass of int) so a
    # future `return <predicate>` can't be misread as exit code 0/1.
    rc = args.func(args)
    if isinstance(rc, int) and not isinstance(rc, bool):
        sys.exit(rc)
