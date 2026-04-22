#!/usr/bin/env python3
"""Export kayak metadata tables to CSV under data/db/.

Writes one CSV per metadata table, sorted by primary key so diffs are stable
across exports. Excludes the append-only time-series tables and caches
(observation, latest_observation, latest_gauge_observation, pages), plus the
reach.geom column (large WKT LineStrings, regenerable via scripts/trace_reach.py).

Usage:
    python3 scripts/export_metadata.py                # uses ../DB/kayak.db
    python3 scripts/export_metadata.py --db /path.db
    python3 scripts/export_metadata.py --out data/db  # default
"""

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent

METADATA_TABLES = [
    "state",
    "source",
    "gauge",
    "gauge_source",
    "fetch_url",
    "calc_expression",
    "rating",
    "rating_data",
    "class_description",
    "guidebook",
    "reach",
    "reach_state",
    "reach_class",
    "reach_guidebook",
    "huc_name",
]

# Columns excluded per-table (regenerable or too large for text VCS).
EXCLUDED_COLUMNS = {
    "reach": {"geom"},
    # last_fetched_at gets bumped on every pipeline run — pure churn in git.
    "fetch_url": {"last_fetched_at"},
}


def table_columns(conn: sqlite3.Connection, table: str) -> list[tuple[str, bool]]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [(row[1], bool(row[5])) for row in rows]  # (name, is_pk)


def export_table(conn: sqlite3.Connection, table: str, out_dir: Path) -> tuple[int, int]:
    cols_info = table_columns(conn, table)
    excluded = EXCLUDED_COLUMNS.get(table, set())
    cols = [name for name, _ in cols_info if name not in excluded]
    pk_cols = [name for name, is_pk in cols_info if is_pk]

    col_list = ", ".join(f'"{c}"' for c in cols)
    order = ", ".join(f'"{c}"' for c in pk_cols) if pk_cols else "1"
    sql = f"SELECT {col_list} FROM {table} ORDER BY {order}"

    dest = out_dir / f"{table}.csv"
    rows = 0
    with dest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(cols)
        for row in conn.execute(sql):
            writer.writerow(row)
            rows += 1
    return rows, dest.stat().st_size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=str(REPO_DIR.parent / "DB" / "kayak.db"),
        help="Path to SQLite database (default: ../DB/kayak.db)",
    )
    parser.add_argument(
        "--out",
        default=str(REPO_DIR / "data" / "db"),
        help="Output directory (default: data/db)",
    )
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    out_dir = Path(args.out).resolve()

    if not db_path.exists():
        print(f"Error: {db_path} does not exist", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        existing = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = [t for t in METADATA_TABLES if t not in existing]
        if missing:
            print(f"Warning: tables not in DB, skipping: {', '.join(missing)}", file=sys.stderr)

        total_rows = 0
        total_bytes = 0
        print(f"{'Table':<20} {'Rows':>10} {'Bytes':>12}")
        print(f"{'-' * 20} {'-' * 10:>10} {'-' * 12:>12}")
        for table in METADATA_TABLES:
            if table not in existing:
                continue
            rows, size = export_table(conn, table, out_dir)
            print(f"{table:<20} {rows:>10} {size:>12}")
            total_rows += rows
            total_bytes += size
        print(f"{'-' * 20} {'-' * 10:>10} {'-' * 12:>12}")
        print(f"{'TOTAL':<20} {total_rows:>10} {total_bytes:>12}")
        print(f"\nWrote to: {out_dir}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
