#!/usr/bin/env python3
"""Import kayak metadata from data/db/*.csv into a SQLite database.

Uses INSERT OR REPLACE keyed on each table's primary key. This is upsert
semantics: existing rows are updated, missing rows are inserted, but rows
present in the DB but absent from the CSVs are NOT deleted. That's deliberate
— it lets you run this against a live DB without cascade-nuking observations
that reference sources you're not touching.

To make the DB exactly match the CSVs, start from a clean schema:
    levels init-db --no-seed   # empty tables + stamped migrations, no YAML seed
    python3 scripts/import_metadata.py

``--no-seed`` matters: a plain ``levels init-db`` also seeds source rows from
sources.yaml with fresh autoincrement ids, which then collide by name with the
canonical-id rows this script loads from source.csv (Source.name is not
unique), leaving duplicate sources. ``--no-seed`` gives empty tables so the CSV
ids load cleanly.

Usage:
    python3 scripts/import_metadata.py                # uses ../DB/kayak.db
    python3 scripts/import_metadata.py --db /path.db
    python3 scripts/import_metadata.py --in data/db   # default
"""

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

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
    except Exception:
        pass
    return REPO_DIR.parent / "DB" / "kayak.db"


# Load order respects foreign-key dependencies: parents before children.
LOAD_ORDER = [
    "state",
    "class_description",
    "guidebook",
    "fetch_url",
    "calc_expression",
    "rating",
    "rating_data",
    "source",
    "gauge",
    "gauge_source",
    "reach",
    "reach_state",
    "reach_class",
    "reach_guidebook",
    "huc_name",
]


def import_table(conn: sqlite3.Connection, csv_path: Path) -> int:
    table = csv_path.stem
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return 0
        cols = ", ".join(f'"{c}"' for c in header)
        placeholders = ", ".join("?" for _ in header)
        sql = f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({placeholders})"

        rows = 0
        batch: list[tuple] = []
        for row in reader:
            # Convert empty strings to NULL; sqlite3 would otherwise store "".
            batch.append(tuple(v if v != "" else None for v in row))
            if len(batch) >= 1000:
                conn.executemany(sql, batch)
                rows += len(batch)
                batch = []
        if batch:
            conn.executemany(sql, batch)
            rows += len(batch)
    return rows


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
        default=str(REPO_DIR / "data" / "db"),
        help="Input directory (default: data/db)",
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
        total_rows = 0
        print(f"{'Table':<20} {'Rows':>10}")
        print(f"{'-' * 20} {'-' * 10:>10}")
        with conn:
            for table in LOAD_ORDER:
                csv_path = in_dir / f"{table}.csv"
                if not csv_path.exists():
                    continue
                rows = import_table(conn, csv_path)
                print(f"{table:<20} {rows:>10}")
                total_rows += rows

            # reach.geom lives in reaches.json (excluded from reach.csv —
            # large + not regenerable on prod); apply it to the reach rows
            # just loaded.
            reaches_json = in_dir / "reaches.json"
            if reaches_json.exists():
                with reaches_json.open(encoding="utf-8") as f:
                    geoms = json.load(f)
                conn.executemany(
                    "UPDATE reach SET geom = ? WHERE id = ?",
                    [(geom, int(rid)) for rid, geom in geoms.items()],
                )
                print(f"{'reaches.json (geom)':<20} {len(geoms):>10}")
        print(f"{'-' * 20} {'-' * 10:>10}")
        print(f"{'TOTAL':<20} {total_rows:>10}")

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
        print(f"\nLoaded into: {db_path}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
