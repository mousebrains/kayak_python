"""``levels recover-metadata`` — reconstruct the dataset CSVs from a database.

A **recovery-only** command: it dumps the metadata tables of a SQLite DB back to
the ``data/db/*.csv`` + ``reaches.json`` / ``reaches-gradient.json`` shape so a
lost or corrupted ``kayak_data`` checkout can be regenerated from a DB backup (or
a dev re-trace can be projected into reviewable CSVs). The reviewed-CSV →
``levels sync-metadata`` flow remains the *only* way metadata reaches the live DB;
this is its inverse, used out-of-band and reviewed via a normal ``kayak_data`` PR.

It replaces the retired nightly reverse-sync (``scripts/snapshot_metadata.sh`` →
``scripts/export_metadata.py``): with the editor write path off in prod there is
no live DB→dataset reconciliation to run on a timer, so the export is demoted from
an automated writer to this hand-run recovery tool.

Writes one CSV per metadata table, sorted by primary key so diffs are stable.
Exports :data:`kayak.dataset.layout.RECOVER_EXPORT_CSVS` — every contract CSV
EXCEPT the generator-owned source/fetch_url/gauge_source trio (those are written
only by ``levels generate-sources`` from the dataset's ``sources.yaml``). The
large ``reach.geom`` / ``reach.gradient_profile`` columns go to the two JSON
sidecars (keyed by reach id) rather than ``reach.csv`` — they'd bloat every
metadata-row diff and aren't regenerable on prod (the DEM/NHD/HUC trace stack is
dev-only). See review-3 R6.1.

Recovery semantics:

* ``--out`` is **required** (no ``DATASET_DIR`` default) and **refused if it
  points inside the active ``DATASET_DIR``** — recovery output must land in a
  scratch directory and be reviewed via a dataset PR, never overwrite the live
  checkout in place.
* ``--db`` is read-only (the DB is never mutated), defaulting to the configured
  ``DATABASE_URL``, so no production-write interlock is needed.

    levels recover-metadata --out /tmp/recovered            # from the configured DB
    levels recover-metadata --db /path/backup.db --out /tmp/recovered
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

from kayak.config import DATASET_DIR
from kayak.dataset import layout
from kayak.db.safety import resolve_db_path

# The recovery writer and the validator share ONE contract — the tables to export
# (in order) and the columns held out to the JSON sidecars both come from
# kayak.dataset.layout so they can't drift.
# (reach.geom + reach.gradient_profile are large, machine-generated, and not
# regenerable on prod, so each goes to its own JSON. See review-3 R6.1 /
# layout.EXCLUDED_COLUMNS.)
#
# RECOVER_EXPORT_CSVS is every contract CSV EXCEPT the generator-owned
# source/fetch_url/gauge_source trio (dataset-separation S1): `levels
# generate-sources` is their sole writer, so a recovery dump must not emit them —
# regenerate those from the dataset's sources.yaml instead. They remain part of
# the dataset (required + synced); only this export side excludes them.
METADATA_TABLES = list(layout.RECOVER_EXPORT_CSVS)
EXCLUDED_COLUMNS = layout.EXCLUDED_COLUMNS


def table_columns(conn: sqlite3.Connection, table: str) -> list[tuple[str, bool]]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [(row[1], bool(row[5])) for row in rows]  # (name, is_pk)


def _round_scales(table: str) -> dict[str, int]:
    """``{column: scale}`` for the table's fixed-precision Numeric columns.

    The DB stores coordinates as full-precision floats (NHD traces emit 13-15
    decimal places), but the schema declares Numeric(9, 6) -- ~0.1 m resolution,
    which is ample. Quantizing on export makes the CSV conform to the declared
    scale (so ``levels validate-dataset`` can enforce it) and drops meaningless
    float-formatting noise from every diff.
    """
    return {s.name: s.decimal_spec[1] for s in layout.column_specs(table) if s.decimal_spec}


def _quantize(value: object, scale: int) -> object:
    """Round a numeric cell to ``scale`` decimal places, dropping trailing zeros.

    Non-numeric / empty values pass through untouched (the validator flags them).
    """
    if value is None or value == "":
        return value
    try:
        q = Decimal(str(value)).quantize(Decimal(1).scaleb(-scale))
    except (ArithmeticError, ValueError):
        return value
    return format(q.normalize(), "f")  # plain decimal, no exponent, no trailing zeros


def export_table(conn: sqlite3.Connection, table: str, out_dir: Path) -> tuple[int, int]:
    cols_info = table_columns(conn, table)
    excluded = EXCLUDED_COLUMNS.get(table, set())
    cols = [name for name, _ in cols_info if name not in excluded]
    pk_cols = [name for name, is_pk in cols_info if is_pk]
    scales = _round_scales(table)

    col_list = ", ".join(f'"{c}"' for c in cols)
    order = ", ".join(f'"{c}"' for c in pk_cols) if pk_cols else "1"
    sql = f"SELECT {col_list} FROM {table} ORDER BY {order}"

    dest = out_dir / f"{table}.csv"
    rows = 0
    with dest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(cols)
        for row in conn.execute(sql):
            writer.writerow(
                [
                    _quantize(v, scales[c]) if c in scales else v
                    for c, v in zip(cols, row, strict=True)
                ]
            )
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
    large (~83% of reach.csv) and not regenerable on prod, so it's exported here
    rather than carried in reach.csv. Loaded by scripts/import_metadata.py via
    ``UPDATE reach SET gradient_profile``. review-3 R6.1.
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


def _refuse_in_dataset(out_dir: Path) -> str | None:
    """Reject an ``--out`` that would write into the active ``DATASET_DIR``.

    Recovery output is reviewed via a dataset PR, so it must land in a scratch
    directory — never overwrite the live checkout in place. Returns an error
    message to abort with, or ``None`` to proceed.
    """
    dataset = DATASET_DIR.resolve()
    if out_dir == dataset or dataset in out_dir.parents:
        return (
            f"--out ({out_dir}) is inside the active DATASET_DIR ({dataset}); "
            "recover-metadata must write to a scratch directory whose output is "
            "reviewed via a kayak_data PR, not overwrite the live checkout in place."
        )
    return None


def recover_metadata(args: argparse.Namespace) -> int:
    """Entry point for ``levels recover-metadata``."""
    db_path = resolve_db_path(args.db).resolve()
    out_dir = Path(args.out).resolve()

    if not db_path.exists():
        print(f"error: {db_path} does not exist", file=sys.stderr)
        return 1

    refusal = _refuse_in_dataset(out_dir)
    if refusal is not None:
        print(f"error: {refusal}", file=sys.stderr)
        return 1

    print(
        "recover-metadata: reconstructing dataset CSVs from a DB for RECOVERY/review "
        "— this is the inverse of `levels sync-metadata`, NOT a deploy path. Review "
        "the output via a kayak_data PR before it becomes the dataset.",
        file=sys.stderr,
    )

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


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the 'recover-metadata' subcommand."""
    parser = subparsers.add_parser(
        "recover-metadata",
        help="Recovery-only: reconstruct dataset CSVs + JSON sidecars from a DB "
        "(inverse of sync-metadata; output reviewed via a kayak_data PR)",
    )
    parser.set_defaults(func=recover_metadata)
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite URL/path to read (read-only; default: the configured DATABASE_URL)",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Scratch output directory for the reconstructed CSVs + JSON sidecars. "
        "Must NOT be inside DATASET_DIR (recovery output is reviewed via a PR, not "
        "applied in place).",
    )
