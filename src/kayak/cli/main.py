"""Argparse CLI entry point (replaces individual C++ programs)."""

import argparse
import sys

from kayak import __version__
from kayak.cli import (
    analyze_logs,
    assign_huc,
    audit_gauges,
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
    fetch_licor,
    fetch_osmb,
    fetch_usgs_ogc,
    generate_sources,
    import_metadata,
    init_dataset,
    init_db,
    migrate,
    orphan_check,
    pipeline,
    recover_metadata,
    render_serving,
    render_units,
    seed_maintainer,
    status,
    sync_metadata,
    trace_reach,
    validate_config,
    validate_dataset,
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
    init_dataset.addArgs(subparsers)
    migrate.addArgs(subparsers)
    fetch.addArgs(subparsers)
    fetch_licor.addArgs(subparsers)
    fetch_osmb.addArgs(subparsers)
    fetch_usgs_ogc.addArgs(subparsers)
    calc_rating.addArgs(subparsers)
    calculator.addArgs(subparsers)
    build.addArgs(subparsers)
    decimate.addArgs(subparsers)
    orphan_check.addArgs(subparsers)
    check_reaches.addArgs(subparsers)
    audit_gauges.addArgs(subparsers)
    pipeline.addArgs(subparsers)
    seed_maintainer.addArgs(subparsers)
    delete_editor.addArgs(subparsers)
    export_editor.addArgs(subparsers)
    editor_retention.addArgs(subparsers)
    trace_reach.addArgs(subparsers)
    assign_huc.addArgs(subparsers)
    emit_config.addArgs(subparsers)
    validate_config.addArgs(subparsers)
    validate_dataset.addArgs(subparsers)
    analyze_logs.addArgs(subparsers)
    status.addArgs(subparsers)
    sync_metadata.addArgs(subparsers)
    render_units.addArgs(subparsers)
    render_serving.addArgs(subparsers)
    import_metadata.addArgs(subparsers)
    recover_metadata.addArgs(subparsers)
    generate_sources.addArgs(subparsers)
    generate_sources.add_source_args(subparsers)

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


if __name__ == "__main__":
    # `python -m kayak.cli.main …` runs the same CLI as the `levels` console
    # script. Without this guard module-style invocation exits 0 having done
    # nothing — a silent footgun if a CI gate ever spells the command that way.
    main()
