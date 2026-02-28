"""Argparse CLI entry point (replaces individual C++ programs)."""

import argparse
import sys

from kayak.cli import build, calc_rating, calculator, fetch, fetch_usgs_ogc, init_db, merge, pipeline
from kayak.cli.logger import addArgs as addLoggerArgs
from kayak.cli.logger import mkLogger


def main():
    """levels - River level data aggregation from government agencies."""
    parser = argparse.ArgumentParser(
        prog="levels",
        description="River level data aggregation from government agencies",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")

    addLoggerArgs(parser)

    subparsers = parser.add_subparsers(dest="command")

    init_db.addArgs(subparsers)
    fetch.addArgs(subparsers)
    fetch_usgs_ogc.addArgs(subparsers)
    merge.addArgs(subparsers)
    calc_rating.addArgs(subparsers)
    calculator.addArgs(subparsers)
    build.addArgs(subparsers)
    pipeline.addArgs(subparsers)

    args = parser.parse_args()
    mkLogger(args)

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)
