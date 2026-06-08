#!/usr/bin/env python3
"""Import kayak metadata from data/db/*.csv into a SQLite database.

Upserts each row with ``INSERT … ON CONFLICT(<pk>) DO UPDATE``: existing rows
are updated, missing rows inserted, and rows present in the DB but absent from
the CSVs are left alone (so this is safe to run against a live DB without
cascade-nuking observations that reference sources you're not touching).

Columns a table deliberately keeps out of its CSV — ``reach.geom`` and
``reach.gradient_profile`` (carried in the JSON sidecars) — are **preserved**
on existing rows. That's the key difference from the old ``INSERT OR REPLACE``,
whose delete-and-reinsert reset those columns to NULL on every import.

To make the DB exactly match the CSVs, start from a clean schema:
    levels init-db --no-seed   # empty tables + stamped migrations, no YAML seed
    python3 scripts/import_metadata.py

``--no-seed`` is required when loading into a freshly-init'd DB: a plain
``levels init-db`` seeds ``state`` / ``source`` / ``fetch_url`` from
sources.yaml with fresh autoincrement ids. Re-importing the canonical-id CSV
rows on top then collides — a duplicate ``source`` (its name isn't unique), or,
because the upsert keys on the primary key, an *aborting* ``UNIQUE`` conflict on
``state.name`` / ``fetch_url.url`` (the old ``INSERT OR REPLACE`` masked these
by delete+reinsert; the upsert surfaces them instead, rolling back the load).
``--no-seed`` gives empty tables so the CSV ids load cleanly.

reach.geom and reach.gradient_profile are excluded from reach.csv and loaded
from data/db/reaches.json and data/db/reaches-gradient.json via
``UPDATE reach SET geom`` / ``SET gradient_profile``. To apply just one to a
live prod DB without re-syncing metadata from the CSVs — e.g. after a re-trace —
use ``--geom-only`` or ``--gradient-only``; pass **both** to apply both JSONs
while still skipping the CSV upsert (it is *not* a no-op).

Usage:
    python3 scripts/import_metadata.py                  # uses ../DB/kayak.db
    python3 scripts/import_metadata.py --db /path.db
    python3 scripts/import_metadata.py --in data/db     # default
    python3 scripts/import_metadata.py --geom-only      # only reaches.json geom
    python3 scripts/import_metadata.py --gradient-only  # only reaches-gradient.json
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from kayak.config import DATASET_DIR
from kayak.db import metadata_csv as mc

REPO_DIR = Path(__file__).resolve().parent.parent


def _default_db_path() -> Path:
    """Resolve the DB path the way ``levels`` does (via ``DATABASE_URL``).

    Keeps this script and ``levels init-db`` pointed at the same file even when
    the operator has set ``DATABASE_URL`` in ``~/.config/kayak/.env``. Falls
    back to ``../DB/kayak.db`` if the package isn't importable.
    """
    try:
        from sqlalchemy.engine import make_url

        from kayak.config import DATABASE_URL

        db = make_url(DATABASE_URL).database
        if db:
            return Path(db)
    except Exception as exc:  # import/resolve failure — fall back, but say so
        print(
            f"Note: couldn't resolve DATABASE_URL ({exc}); falling back to ../DB/kayak.db",
            file=sys.stderr,
        )
    return REPO_DIR.parent / "DB" / "kayak.db"


def _load_csvs(conn: sqlite3.Connection, in_dir: Path) -> int:
    """Upsert every metadata CSV present via ``kayak.db.metadata_csv``, print a
    per-table line, and return the total rows processed. (The upsert helpers
    moved into the package so ``levels sync-metadata`` shares one
    implementation; this stays a thin wrapper for the script's output.)"""
    counts = mc.upsert_csvs(conn, in_dir)
    for table, rows in counts.items():
        print(f"{table:<20} {rows:>10}")
    return sum(counts.values())


def _apply_geom(conn: sqlite3.Connection, in_dir: Path) -> None:
    """Apply reach.geom from reaches.json (excluded from reach.csv).

    Reports the rows actually updated (``cur.rowcount``), not the snapshot
    size, so a mis-resolved or empty DB shows 0 rather than a falsely-full
    count. Flags any snapshot reaches that matched no row in this DB.
    """
    reaches_json = in_dir / "reaches.json"
    if not reaches_json.exists():
        return
    with reaches_json.open(encoding="utf-8") as f:
        # Fail cleanly (and roll back the enclosing transaction) on a corrupt
        # snapshot rather than dumping a raw traceback — reaches.json is
        # machine-generated, so a malformed one is a real problem to surface.
        try:
            geoms = json.load(f)
            pairs = [(geom, int(rid)) for rid, geom in geoms.items()]
        except (json.JSONDecodeError, ValueError, AttributeError) as exc:
            print(f"Error: {reaches_json} is malformed ({exc})", file=sys.stderr)
            raise SystemExit(1) from exc
    cur = conn.executemany("UPDATE reach SET geom = ? WHERE id = ?", pairs)
    applied = cur.rowcount
    print(f"{'reaches.json (geom)':<20} {applied:>10}")
    if applied != len(geoms):
        print(
            f"Note: {len(geoms)} reaches in reaches.json but {applied} matched a "
            "reach row (the rest have no row in this DB).",
            file=sys.stderr,
        )


def _apply_gradient(conn: sqlite3.Connection, in_dir: Path) -> None:
    """Apply reach.gradient_profile from reaches-gradient.json (excluded from
    reach.csv). Mirrors _apply_geom: reports rows actually updated and flags any
    snapshot reaches that matched no row in this DB. review-3 R6.1.
    """
    grad_json = in_dir / "reaches-gradient.json"
    if not grad_json.exists():
        return
    with grad_json.open(encoding="utf-8") as f:
        try:
            grads = json.load(f)
            pairs = [(gp, int(rid)) for rid, gp in grads.items()]
        except (json.JSONDecodeError, ValueError, AttributeError) as exc:
            print(f"Error: {grad_json} is malformed ({exc})", file=sys.stderr)
            raise SystemExit(1) from exc
    cur = conn.executemany("UPDATE reach SET gradient_profile = ? WHERE id = ?", pairs)
    applied = cur.rowcount
    print(f"{'reaches-gradient.json':<20} {applied:>10}")
    if applied != len(grads):
        print(
            f"Note: {len(grads)} reaches in reaches-gradient.json but {applied} matched a "
            "reach row (the rest have no row in this DB).",
            file=sys.stderr,
        )


def _report_integrity(conn: sqlite3.Connection) -> int:
    """integrity_check (hard fail) + foreign_key_check (informational).

    Returns a process exit code: 1 if the DB is corrupt, else 0.
    """
    (check,) = conn.execute("PRAGMA integrity_check").fetchone()
    if check != "ok":
        print(f"Integrity check failed: {check}", file=sys.stderr)
        return 1

    fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_violations:
        by_table: dict[str, int] = {}
        for row in fk_violations:
            by_table[row[0]] = by_table.get(row[0], 0) + 1
        print("\nFK violations detected (informational):", file=sys.stderr)
        for tbl, count in sorted(by_table.items(), key=lambda x: -x[1]):
            print(f"  {tbl:<20} {count:>10}", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=None,
        help="Path to SQLite database (default: the configured DATABASE_URL, "
        "matching `levels`; falls back to ../DB/kayak.db)",
    )
    parser.add_argument(
        "--in",
        dest="in_dir",
        default=str(DATASET_DIR),
        help="Input directory (default: the configured DATASET_DIR — data/db, "
        "or the kayak_data clone post-split)",
    )
    parser.add_argument(
        "--geom-only",
        action="store_true",
        help="Load only reaches.json (reach.geom); skip the CSV upsert. Use to "
        "apply geometry to a live DB without re-syncing metadata from the CSVs.",
    )
    parser.add_argument(
        "--gradient-only",
        action="store_true",
        help="Load only reaches-gradient.json (reach.gradient_profile); skip the "
        "CSV upsert. Parallel to --geom-only. Passing both --geom-only and "
        "--gradient-only applies both JSONs and still skips the CSV upsert.",
    )
    args = parser.parse_args()

    db_path = (Path(args.db) if args.db else _default_db_path()).resolve()
    in_dir = Path(args.in_dir).resolve()

    if not db_path.exists():
        print(f"Error: {db_path} does not exist; run `levels init-db` first", file=sys.stderr)
        return 1
    if not in_dir.exists():
        print(f"Error: {in_dir} does not exist", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    # FK enforcement is off during the load to mirror the source DB's state
    # (the live DB has accumulated orphan rows and enforces FKs at application
    # level, not DB level). We run foreign_key_check afterwards and report
    # violations as warnings.
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        print(f"{'Table':<20} {'Rows':>10}")
        print(f"{'-' * 20} {'-' * 10:>10}")
        with conn:
            # --geom-only / --gradient-only each skip the CSV upsert; passing
            # BOTH means "apply both JSON blobs, still skip the CSV" — not a
            # silent no-op. A blob loads unless the *other* "-only" flag selected
            # exclusively away from it: geom loads unless gradient-only-and-not-
            # geom-only, gradient loads unless geom-only-and-not-gradient-only.
            snapshot_only = args.geom_only or args.gradient_only
            total_rows = 0 if snapshot_only else _load_csvs(conn, in_dir)
            if args.geom_only or not args.gradient_only:
                _apply_geom(conn, in_dir)
            if args.gradient_only or not args.geom_only:
                _apply_gradient(conn, in_dir)
        print(f"{'-' * 20} {'-' * 10:>10}")
        print(f"{'TOTAL':<20} {total_rows:>10}")

        rc = _report_integrity(conn)
        if rc:
            return rc
        print(f"\nLoaded into: {db_path}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
