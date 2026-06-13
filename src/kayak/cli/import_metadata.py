"""``levels import-metadata`` — apply the reach geometry/gradient sidecars.

The packaged equivalent of ``scripts/import_metadata.py`` (which now
delegates to the same :mod:`kayak.db.sidecars` functions): ``reaches.json``
and ``reaches-gradient.json`` are dataset content excluded from
``reach.csv``, so ``sync-metadata`` never writes ``reach.geom`` /
``reach.gradient_profile`` — this command is the deploy step that does,
after sync and before build. ``kayak-deploy`` activation runs it from the
release venv (PR #190 review: a sidecar-only dataset release must reach
the DB).

Default applies BOTH sidecars; ``--geom-only`` / ``--gradient-only``
narrow. Unmatched sidecar ids (a snapshot reach with no row in this DB)
roll the apply back and exit non-zero unless ``--allow-missing-reaches``.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

from kayak.db import sidecars

EXIT_OK = 0
EXIT_ERROR = 1


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "import-metadata",
        help="Apply reach geom/gradient JSON sidecars from DATASET_DIR (after sync-metadata)",
    )
    parser.set_defaults(func=import_metadata)
    only = parser.add_mutually_exclusive_group()
    only.add_argument(
        "--geom-only", action="store_true", help="Apply only reaches.json (reach.geom)"
    )
    only.add_argument(
        "--gradient-only",
        action="store_true",
        help="Apply only reaches-gradient.json (reach.gradient_profile)",
    )
    parser.add_argument(
        "--allow-missing-reaches",
        action="store_true",
        help="Permit a partial apply when sidecar ids have no reach row (default: "
        "roll back and fail — the usual cause is running before sync-metadata)",
    )


def import_metadata(args: argparse.Namespace) -> int:
    from kayak.config import DATABASE_URL, DATASET_DIR

    db = make_url(DATABASE_URL).database
    if not db or not Path(db).exists():
        print(f"ERROR: database not found ({db!r}); run `levels init-db` first", file=sys.stderr)
        return EXIT_ERROR
    dataset_dir = Path(DATASET_DIR)
    if not dataset_dir.is_dir():
        print(f"ERROR: DATASET_DIR not found: {dataset_dir}", file=sys.stderr)
        return EXIT_ERROR

    do_geom = args.geom_only or not args.gradient_only
    do_gradient = args.gradient_only or not args.geom_only

    conn = sqlite3.connect(db)
    # FK enforcement off during the apply (matches scripts/import_metadata.py:
    # the live DB enforces FKs at application level; the legacy rows carry
    # intentional orphans).
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        print(f"{'Sidecar':<22} {'Rows':>10}")
        try:
            with conn:
                unmatched = 0
                if do_geom:
                    applied, miss = sidecars.apply_geom(conn, dataset_dir)
                    print(f"{'reaches.json (geom)':<22} {applied:>10}")
                    unmatched += miss
                if do_gradient:
                    applied, miss = sidecars.apply_gradient(conn, dataset_dir)
                    print(f"{'reaches-gradient.json':<22} {applied:>10}")
                    unmatched += miss
                if unmatched and not args.allow_missing_reaches:
                    raise _MissingReaches(unmatched)
        except _MissingReaches as exc:
            print(
                f"ERROR: {exc.count} sidecar entr(ies) matched no reach row — nothing "
                "applied. Run `levels sync-metadata` first, or pass "
                "--allow-missing-reaches for a deliberate partial apply.",
                file=sys.stderr,
            )
            return EXIT_ERROR
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return EXIT_ERROR
    finally:
        conn.close()
    return EXIT_OK


class _MissingReaches(Exception):
    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(count)
