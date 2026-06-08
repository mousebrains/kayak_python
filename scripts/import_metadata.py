#!/usr/bin/env python3
"""Apply the reach geometry/gradient JSON sidecars to a SQLite database.

``reach.geom`` and ``reach.gradient_profile`` are excluded from ``reach.csv`` (large,
machine-generated, not regenerable on prod) and live in ``reaches.json`` /
``reaches-gradient.json`` in the dataset. This script applies them via
``UPDATE reach SET geom`` / ``SET gradient_profile`` — the **only** path that writes
those two columns to a live DB. ``levels sync-metadata`` applies every *CSV* column
(by stable id, with delete-safety) but deliberately excludes these sidecars; deploy.sh
steps 3.25/3.26 invoke this script for them.

So the CSV half of a fresh load or a metadata change goes through ``levels
sync-metadata``; this script only touches the geometry sidecars. (It is the sanctioned
sidecar applier — like ``sync-metadata`` it *is* an apply path, so it does not carry the
``refuse_configured_db`` interlock the authoring tools do.)

Usage:
    python3 scripts/import_metadata.py                  # apply BOTH sidecars
    python3 scripts/import_metadata.py --geom-only      # only reaches.json (geom)
    python3 scripts/import_metadata.py --gradient-only  # only reaches-gradient.json
    python3 scripts/import_metadata.py --db /path.db --in <dataset dir>

Fresh-DB load:
    levels init-db --no-seed              # empty tables + stamped migrations
    levels sync-metadata                  # CSV metadata, matched by id (delete-safe)
    python3 scripts/import_metadata.py    # geom + gradient sidecars
    levels pipeline
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from kayak.config import DATASET_DIR

REPO_DIR = Path(__file__).resolve().parent.parent


class _MissingReaches(RuntimeError):
    """A sidecar carried reach ids with no matching reach row — roll back, fail loud."""

    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(count)


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


def _apply_geom(conn: sqlite3.Connection, in_dir: Path) -> int:
    """Apply reach.geom from reaches.json (excluded from reach.csv).

    Reports the rows actually updated (``cur.rowcount``), not the snapshot
    size, so a mis-resolved or empty DB shows 0 rather than a falsely-full
    count. Returns the number of snapshot reaches that matched NO row in this
    DB (0 = clean) so the caller can fail loud on the most likely operator
    mistake — applying the sidecars before ``levels sync-metadata`` (or to the
    wrong/empty DB).
    """
    reaches_json = in_dir / "reaches.json"
    if not reaches_json.exists():
        return 0
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
    print(f"{'reaches.json (geom)':<22} {applied:>10}")
    unmatched = len(geoms) - applied
    if unmatched:
        print(
            f"Warning: {len(geoms)} reaches in reaches.json but only {applied} matched a "
            "reach row (the rest have no row in this DB).",
            file=sys.stderr,
        )
    return unmatched


def _apply_gradient(conn: sqlite3.Connection, in_dir: Path) -> int:
    """Apply reach.gradient_profile from reaches-gradient.json (excluded from
    reach.csv). Mirrors _apply_geom: reports rows actually updated and returns the
    number of snapshot reaches that matched no row in this DB. review-3 R6.1.
    """
    grad_json = in_dir / "reaches-gradient.json"
    if not grad_json.exists():
        return 0
    with grad_json.open(encoding="utf-8") as f:
        try:
            grads = json.load(f)
            pairs = [(gp, int(rid)) for rid, gp in grads.items()]
        except (json.JSONDecodeError, ValueError, AttributeError) as exc:
            print(f"Error: {grad_json} is malformed ({exc})", file=sys.stderr)
            raise SystemExit(1) from exc
    cur = conn.executemany("UPDATE reach SET gradient_profile = ? WHERE id = ?", pairs)
    applied = cur.rowcount
    print(f"{'reaches-gradient.json':<22} {applied:>10}")
    unmatched = len(grads) - applied
    if unmatched:
        print(
            f"Warning: {len(grads)} reaches in reaches-gradient.json but only {applied} matched a "
            "reach row (the rest have no row in this DB).",
            file=sys.stderr,
        )
    return unmatched


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
        help="Directory holding reaches.json / reaches-gradient.json (default: the "
        "configured DATASET_DIR — the kayak_data clone post-split)",
    )
    parser.add_argument(
        "--geom-only",
        action="store_true",
        help="Apply only reaches.json (reach.geom). Default (no flag) applies both sidecars.",
    )
    parser.add_argument(
        "--gradient-only",
        action="store_true",
        help="Apply only reaches-gradient.json (reach.gradient_profile). Default applies both.",
    )
    parser.add_argument(
        "--allow-missing-reaches",
        action="store_true",
        help="Allow a partial apply: don't fail if a sidecar reach id has no reach row "
        "in this DB. Default is to roll back and exit non-zero — the usual cause is "
        "running this before `levels sync-metadata` (or against the wrong/empty DB).",
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

    # A bare run applies BOTH sidecars; each "-only" flag narrows to just that one.
    apply_geom = args.geom_only or not args.gradient_only
    apply_gradient = args.gradient_only or not args.geom_only

    conn = sqlite3.connect(db_path)
    # FK enforcement is off during the apply to mirror the source DB's state (the
    # live DB enforces FKs at application level, not DB level); foreign_key_check
    # runs afterwards and reports any violations as warnings.
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        print(f"{'Sidecar':<22} {'Rows':>10}")
        print(f"{'-' * 22} {'-' * 10:>10}")
        try:
            with conn:
                unmatched = 0
                if apply_geom:
                    unmatched += _apply_geom(conn, in_dir)
                if apply_gradient:
                    unmatched += _apply_gradient(conn, in_dir)
                # Fail loud (rolling back the partial apply) when a sidecar id has no
                # reach row — almost always "ran before `levels sync-metadata`" or the
                # wrong/empty DB. --allow-missing-reaches opts into a partial apply.
                if unmatched and not args.allow_missing_reaches:
                    raise _MissingReaches(unmatched)
        except _MissingReaches as exc:
            print(
                f"error: {exc.count} sidecar entr(ies) matched no reach row in this DB "
                "(summed across the JSON sidecars applied) — nothing applied. Run "
                "`levels sync-metadata` first, or pass --allow-missing-reaches for a "
                "deliberate partial apply.",
                file=sys.stderr,
            )
            return 1

        rc = _report_integrity(conn)
        if rc:
            return rc
        print(f"\nApplied geometry sidecars into: {db_path}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
