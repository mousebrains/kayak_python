"""Check for fetch-active source rows that aren't linked to any gauge.

A source with a ``fetch_url_id`` but no ``gauge_source`` row is an
orphan: the fetch pipeline keeps it fed with fresh observations, but
nothing consumes that data. Orphans typically appear when a deletion
migration removes a source row without deactivating its fetch_url —
the next ``levels fetch`` then auto-creates a replacement Source
without a gauge_source link (parsers/base.py::_auto_create_source).

See ``docs/PLAN_orphan_sources.md`` for the systemic context and the
follow-up pipeline integration that escalates these to alerts.
"""

import argparse
import json
import sys

from kayak.db.engine import get_session
from kayak.db.sources import find_orphan_sources


def addArgs(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the 'orphan-check' subcommand."""
    parser = subparsers.add_parser(
        "orphan-check",
        help="List fetch-active source rows with no gauge_source link",
    )
    parser.set_defaults(func=orphan_check)
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit JSON instead of a human-readable table",
    )
    parser.add_argument(
        "--exit-nonzero-if-found",
        action="store_true",
        help="Exit non-zero if any orphan source is found (for CI / scripting)",
    )


def orphan_check(args: argparse.Namespace) -> None:
    """Print orphan source rows, optionally exiting non-zero if any."""
    session = get_session()
    try:
        rows = find_orphan_sources(session)
    finally:
        session.close()

    if args.as_json:
        json.dump(
            [
                {
                    "source_id": r.source_id,
                    "name": r.name,
                    "agency": r.agency,
                    "url": r.url,
                    "is_active": r.is_active,
                    "latest_obs": r.latest_obs.isoformat() if r.latest_obs else None,
                }
                for r in rows
            ],
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
    elif not rows:
        print("No orphan sources.")
    else:
        widths = (5, 24, 10, 60, 6, 19)
        header = ("id", "name", "agency", "url", "active", "latest_obs")
        print(" ".join(f"{h:<{w}}" for h, w in zip(header, widths, strict=True)))
        for r in rows:
            cells = (
                str(r.source_id),
                r.name[: widths[1]],
                (r.agency or "")[: widths[2]],
                r.url[: widths[3]],
                "1" if r.is_active else "0",
                r.latest_obs.strftime("%Y-%m-%d %H:%M:%S") if r.latest_obs else "(none)",
            )
            print(" ".join(f"{c:<{w}}" for c, w in zip(cells, widths, strict=True)))
        print(f"\n{len(rows)} orphan source(s).")

    if rows and args.exit_nonzero_if_found:
        sys.exit(1)
