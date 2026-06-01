"""Apply a reviewed ``data/db/*.csv`` diff to a live DB by stable id.

``levels sync-metadata`` is the redesign's prod-apply path: INSERT new rows,
UPDATE changed rows, and DELETE rows absent from the CSVs — matched by the
stable numeric primary key, so a rename is an UPDATE (not delete+insert) and the
observations of surviving sources are untouched (their integer FKs stay valid
because the id never moves). It replaces "write a data migration, then snapshot
the CSV" with "edit the reviewed CSV, then sync".

    levels sync-metadata --dry-run          # print the plan, change nothing
    levels sync-metadata                     # apply inserts/updates; REFUSE deletes
    levels sync-metadata --allow-deletes     # also apply deletions (drops data)

Deletions are gated: a deleted source's observations are gone forever and are
invisible in the one-line CSV diff, so without ``--allow-deletes`` the sync
applies the safe insert/update half, prints the per-source observation-drop
counts, and exits non-zero (2) so ``deploy.sh`` aborts and a human runs the
delete by hand.

Runs on a **raw ``sqlite3`` connection with ``foreign_keys=ON``** (set before
any transaction opens — SQLite ignores the PRAGMA mid-transaction): the schema's
``ON DELETE CASCADE`` / ``SET NULL`` clean a deleted row's dependents, the
``observation`` ``RESTRICT`` is enforced, and a bad diff fails loudly and rolls
back the whole transaction rather than silently orphaning rows. The SQLAlchemy
engine forces ``foreign_keys=ON`` too but yields a wrapped connection mid-pool;
the raw connection lets the upsert helpers (``kayak.db.metadata_csv``) run
directly and lets us own the PRAGMA timing.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from kayak.config import DATA_DIR, DATABASE_URL
from kayak.db import metadata_csv as mc


class SyncError(RuntimeError):
    """A sync that must roll back (integrity / foreign-key failure)."""


def _resolve_db_path(database_url: str | None) -> Path:
    """The on-disk SQLite path from a ``sqlite://`` URL (or a bare path)."""
    url = database_url or DATABASE_URL
    if "://" in url:
        from sqlalchemy.engine import make_url

        db = make_url(url).database
        if not db:
            raise SyncError(f"no database path in URL: {url!r}")
        return Path(db)
    return Path(url)


def _summarize_violations(rows: list[tuple[object, ...]]) -> str:
    by_table: dict[str, int] = {}
    for row in rows:
        name = str(row[0])
        by_table[name] = by_table.get(name, 0) + 1
    return ", ".join(f"{t}:{n}" for t, n in sorted(by_table.items()))


def _audit_or_raise(conn: sqlite3.Connection) -> None:
    """Assert the post-apply DB is clean; raise (→ rollback) otherwise.

    The fresh live DB has zero ``foreign_key_check`` violations (verified), so
    any violation here is one this sync introduced — i.e. a bad CSV diff. Under
    ``foreign_keys=ON`` a violating statement already raised, but this is the
    belt-and-suspenders end-state check.
    """
    if not mc.integrity_ok(conn):
        raise SyncError("integrity_check failed")
    violations = mc.foreign_key_violations(conn)
    if violations:
        raise SyncError(f"foreign-key violations after sync ({_summarize_violations(violations)})")


def _print_plan(plan: mc.SyncPlan, db_path: Path, csv_dir: Path) -> None:
    """Print the plan unconditionally — it's the command's report (esp. for
    --dry-run), so it must not depend on the log level."""
    print(f"sync-metadata plan  (db={db_path}  csv={csv_dir})")
    tables = sorted(set(plan.insert_pks) | set(plan.delete_pks))
    if not tables:
        print("  (no changes — the DB already matches the CSVs)")
    else:
        print(f"  {'table':<18} {'+insert':>8} {'-delete':>8}")
        for t in tables:
            print(
                f"  {t:<18} "
                f"{len(plan.insert_pks.get(t, set())):>8} "
                f"{len(plan.delete_pks.get(t, set())):>8}"
            )
    if plan.source_obs_drops:
        print("  DELETE would drop observations (IRREVERSIBLE):")
        for sid, n in sorted(plan.source_obs_drops.items()):
            print(f"    source {sid:<8} {n:>12,} observations")
        print(f"  TOTAL observations a delete would drop: {plan.total_obs_dropped:,}")


def sync_metadata(args: argparse.Namespace) -> int:
    """Entry point for ``levels sync-metadata``."""
    db_path = _resolve_db_path(args.database_url)
    csv_dir = Path(args.csv_dir).resolve() if args.csv_dir else DATA_DIR / "db"

    if not db_path.exists():
        print(
            f"error: database does not exist: {db_path} (run `levels init-db` first)",
            file=sys.stderr,
        )
        return 1
    if not csv_dir.exists():
        print(f"error: csv dir does not exist: {csv_dir}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    try:
        # FK enforcement must be enabled BEFORE any transaction opens — SQLite
        # silently ignores the PRAGMA mid-transaction. A fresh connection has no
        # open transaction, and PRAGMA/SELECT don't auto-begin one in Python's
        # sqlite3, so compute_plan's reads below stay in autocommit too.
        conn.execute("PRAGMA foreign_keys = ON")
        if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            print("error: could not enable foreign_keys (an open transaction?)", file=sys.stderr)
            return 1

        plan = mc.compute_plan(conn, csv_dir)
        _print_plan(plan, db_path, csv_dir)

        if args.dry_run:
            print("dry-run: nothing applied")
            return 0

        refuse_deletes = plan.has_deletes and not args.allow_deletes
        try:
            with conn:  # commit on success; ROLLBACK on any raise
                mc.upsert_csvs(conn, csv_dir)
                if plan.has_deletes and not refuse_deletes:
                    deleted = mc.apply_deletions(conn, plan)
                    print(f"deleted {sum(deleted.values())} row(s) across {len(deleted)} table(s)")
                _audit_or_raise(conn)
        except (SyncError, sqlite3.Error) as exc:
            print(f"error: sync rolled back — NO changes applied: {exc}", file=sys.stderr)
            return 1

        if refuse_deletes:
            print(
                f"REFUSED {plan.total_deletes} deletion(s) that would drop "
                f"{plan.total_obs_dropped:,} observation(s); applied inserts/updates only. "
                "Review the plan above, then re-run with --allow-deletes.",
                file=sys.stderr,
            )
            return 2

        print(f"sync-metadata complete → {db_path}")
        return 0
    finally:
        conn.close()


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the 'sync-metadata' subcommand."""
    parser = subparsers.add_parser(
        "sync-metadata",
        help="Apply a reviewed data/db/*.csv diff to the live DB by stable id",
    )
    parser.set_defaults(func=sync_metadata)
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLite URL/path to sync (default: the configured DATABASE_URL)",
    )
    parser.add_argument(
        "--csv-dir",
        default=None,
        help="Directory of metadata CSVs (default: data/db)",
    )
    parser.add_argument(
        "--allow-deletes",
        action="store_true",
        help="Apply DELETEs (rows absent from the CSVs). Without it, deletions are "
        "refused (exit 2) after printing the per-source observation-drop counts.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan and exit without changing anything",
    )
