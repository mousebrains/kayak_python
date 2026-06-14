"""``levels audit-gauges`` — audit gauge metadata against the kayak DB.

Thin CLI wrapper: registers the subcommand and its options, then hands the
parsed namespace to :func:`kayak.gauge_audit.audit.run_audit`, which refreshes
the USGS/NWPS site caches and reports new candidates, stopped/started feeds, and
stale gauges (optionally emailing a digest). The audit logic lives in
:mod:`kayak.gauge_audit.audit`; this module stays fully typed.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from kayak.config import GAUGE_METADATA_CACHE

# Default kayak DB for the audit. The live unit reads the prod DB; ``--kayak-db``
# overrides for a scratch copy. (Kept here, not in the audit module, because it's
# only a CLI default.)
KAYAK_DB = Path.home() / "DB" / "kayak.db"


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the 'audit-gauges' subcommand."""
    parser = subparsers.add_parser(
        "audit-gauges",
        help="Audit gauge metadata: refresh caches, find candidates, detect data changes",
    )
    parser.set_defaults(func=audit_gauges)
    parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="Skip refreshing the metadata caches",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Window in days for data status checks (default: 7)",
    )
    parser.add_argument(
        "--cache-db",
        type=str,
        default=str(GAUGE_METADATA_CACHE),
        help=f"Path to gauge metadata cache (default: {GAUGE_METADATA_CACHE})",
    )
    parser.add_argument(
        "--kayak-db",
        type=str,
        default=str(KAYAK_DB),
        help=f"Path to kayak database (default: {KAYAK_DB})",
    )
    parser.add_argument(
        "--email",
        type=str,
        default=os.environ.get("AUDIT_EMAIL"),
        help="Email digest to this address (or set AUDIT_EMAIL). Always sends if set.",
    )
    parser.add_argument(
        "--candidate-miles",
        type=float,
        default=3.0,
        help="Max distance (mi) from a reach midpoint for a candidate gauge (default: 3)",
    )
    parser.add_argument(
        "--include-gauged",
        action="store_true",
        help="Also list candidates for reaches that already have a linked gauge "
        "(off by default — these are rarely actionable)",
    )


def audit_gauges(args: argparse.Namespace) -> None:
    """Entry point for ``levels audit-gauges``."""
    from kayak.gauge_audit.audit import run_audit

    run_audit(args)
