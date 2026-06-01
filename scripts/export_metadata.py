#!/usr/bin/env python3
"""Export kayak metadata tables to CSV under data/db/.

Writes one CSV per metadata table, sorted by primary key so diffs are stable
across exports. Excludes the append-only time-series tables and caches
(observation, latest_observation, latest_gauge_observation, pages). The
large reach.geom and reach.gradient_profile columns go to separate JSONs
(reaches.json and reaches-gradient.json, keyed by reach id) rather than
reach.csv — they'd bloat every metadata-row diff and aren't regenerable on prod
(the DEM/NHD/HUC trace stack is dev-only), so committing them keeps from-CSV
rebuilds self-contained (map traces + gradient charts render). See R6.1.

Usage:
    python3 scripts/export_metadata.py                # uses ../DB/kayak.db
    python3 scripts/export_metadata.py --db /path.db
    python3 scripts/export_metadata.py --out data/db  # default
"""

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

from kayak.config import METADATA_DIR

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

# Columns excluded from the per-table CSVs (too large for row-based diffs, or
# pure churn).
#
# reach.geom and reach.gradient_profile are both large, machine-generated, and
# not regenerable on prod (the DEM/NHD/HUC trace stack is dev-only), so each is
# excluded from reach.csv and snapshotted to its own JSON instead —
# reaches.json (write_reaches_json) and reaches-gradient.json
# (write_reaches_gradient_json) — keeping rebuilds self-contained without
# bloating every metadata-row diff (gradient_profile was ~83% of reach.csv;
# review-3 R6.1). max_gradient (one float) stays in reach.csv. The full
# huc_name lookup stays in METADATA_TABLES for the same self-contained reason.
EXCLUDED_COLUMNS = {
    "reach": {"geom", "gradient_profile"},
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


def write_reaches_json(conn: sqlite3.Connection, out_dir: Path) -> tuple[int, int]:
    """Write reach.geom to reaches.json, keyed by reach id (numeric order).

    Kept out of reach.csv (large; would bloat every metadata-row diff) but
    committed here because geom is not regenerable on prod. Loaded by
    scripts/import_metadata.py via ``UPDATE reach SET geom``.
    """
    data = {
        str(rid): geom
        for rid, geom in conn.execute(
            "SELECT id, geom FROM reach WHERE geom IS NOT NULL AND geom != '' ORDER BY id"
        )
    }
    dest = out_dir / "reaches.json"
    with dest.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")
    return len(data), dest.stat().st_size


def write_reaches_gradient_json(conn: sqlite3.Connection, out_dir: Path) -> tuple[int, int]:
    """Write reach.gradient_profile to reaches-gradient.json, keyed by reach id.

    Same rationale as write_reaches_json for geom: the per-reach sample JSON is
    large (~83% of reach.csv) and not regenerable on prod, so it's snapshotted
    here rather than carried in reach.csv. Loaded by scripts/import_metadata.py
    via ``UPDATE reach SET gradient_profile``. review-3 R6.1.
    """
    data = {
        str(rid): gp
        for rid, gp in conn.execute(
            "SELECT id, gradient_profile FROM reach "
            "WHERE gradient_profile IS NOT NULL AND gradient_profile != '' ORDER BY id"
        )
    }
    dest = out_dir / "reaches-gradient.json"
    with dest.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")
    return len(data), dest.stat().st_size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=str(REPO_DIR.parent / "DB" / "kayak.db"),
        help="Path to SQLite database (default: ../DB/kayak.db)",
    )
    parser.add_argument(
        "--out",
        default=str(METADATA_DIR),
        help="Output directory (default: the configured METADATA_DIR — data/db, "
        "or the kayak_data clone post-split)",
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

        n_geom, geom_bytes = write_reaches_json(conn, out_dir)
        print(f"reaches.json: {n_geom} reaches, {geom_bytes} bytes (reach.geom)")
        n_grad, grad_bytes = write_reaches_gradient_json(conn, out_dir)
        print(
            f"reaches-gradient.json: {n_grad} reaches, {grad_bytes} bytes (reach.gradient_profile)"
        )
        print(f"\nWrote to: {out_dir}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
