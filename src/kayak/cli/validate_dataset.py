"""``levels validate-dataset <dir>`` — one gate for every dataset invariant.

Consolidates the checks that were scattered across the code repo's
``tests/test_id_counters.py``, ``test_reach_names.py``,
``test_committed_reach_geom.py`` and the data repo's stdlib ``validate.py``
into a single command the engine owns. The code repo runs it against the
fixture (``tests/fixtures/dataset``); the data repo's CI runs it against the
real dataset (S4b). Both get the same authoritative gate, so the two repos
can never drift on what "a valid dataset" means.

S4a scope (this file): CSV parse/headers, id-counter invariants, reach-name
rules, cross-set integrity (JSON snapshot + child-table reach ids are subsets
of ``reach.csv``; every reach carries geometry), and the full
``check-reaches`` geometry validator run against a materialized temp DB. Later
phases extend the same command: generator drift (S1) and the ``dataset.yaml``
contract (S6). The required-file/CSV-header knowledge here is deliberately
local to S4a; S6 promotes it into the versioned contract manifest. The command
takes a REQUIRED explicit directory and never consults METADATA_DIR/DATASET_DIR.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

# Tables (and their id column) whose id-counter invariants we enforce when the
# dataset declares a counter for them. Kept permissive: only counters actually
# present in id_counters.csv are checked, so a minimal dataset need not list
# every table.
_REQUIRED_FILES = ["state.csv", "reach.csv", "id_counters.csv"]
_GEOM_JSON = "reaches.json"
_GRADIENT_JSON = "reaches-gradient.json"
# Child CSVs whose reach_id column must reference an existing reach.
_REACH_CHILDREN = ("reach_state.csv", "reach_class.csv", "reach_guidebook.csv")


def addArgs(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "validate-dataset",
        help="Validate a dataset directory (CSV/JSON integrity + check-reaches)",
    )
    # Required explicit directory. The validator deliberately does NOT consult
    # METADATA_DIR / DATASET_DIR or any deployment setting, so S6's root rename
    # creates no validator-path churn (plan S4a).
    p.add_argument("dir", help="Dataset directory to validate")
    p.set_defaults(func=_main)


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _id_set(path: Path, column: str) -> set[int]:
    return {int(r[column]) for r in _csv_rows(path) if (r.get(column) or "").strip()}


def validate_dataset(dataset_dir: Path) -> list[str]:
    """Return a list of human-readable problems; empty means the dataset is valid."""
    d = dataset_dir
    missing = [f"missing required file: {n}" for n in _REQUIRED_FILES if not (d / n).is_file()]
    if missing:
        return missing  # nothing else is meaningful without the core files
    return [
        *_check_csv_parse(d),
        *_check_id_counters(d),
        *_check_reach_names(d),
        *_check_cross_set(d),
        *_check_reaches_on_materialized(d),
    ]


def _check_csv_parse(d: Path) -> list[str]:
    """Every CSV parses with a header."""
    errors: list[str] = []
    for csv_path in sorted(d.glob("*.csv")):
        try:
            with csv_path.open(encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                if reader.fieldnames is None:
                    errors.append(f"{csv_path.name}: empty / headerless")
                    continue
                for _ in reader:
                    pass
        except (OSError, csv.Error) as exc:
            errors.append(f"{csv_path.name}: unreadable ({exc})")
    return errors


def _check_id_counters(d: Path) -> list[str]:
    """Unique ids per counted table; next_id strictly above the max existing id."""
    errors: list[str] = []
    for row in _csv_rows(d / "id_counters.csv"):
        table, nxt = row["table"], int(row["next_id"])
        tbl_csv = d / f"{table}.csv"
        if not tbl_csv.is_file():
            errors.append(f"id_counters names {table} but {table}.csv is missing")
            continue
        id_list = [int(r["id"]) for r in _csv_rows(tbl_csv) if (r.get("id") or "").strip()]
        if not id_list:
            errors.append(f"{table}.csv (in id_counters) has no id rows")
            continue
        dups = sorted(i for i, c in Counter(id_list).items() if c > 1)
        if dups:
            errors.append(f"{table}.csv has duplicate ids: {dups}")
        if max(id_list) >= nxt:
            errors.append(
                f"{table}: next_id={nxt} <= max id {max(id_list)} (stale counter / id-reuse risk)"
            )
    return errors


def _check_reach_names(d: Path) -> list[str]:
    """reach.name is non-empty and unique (it is a symbolic FK / public handle)."""
    errors: list[str] = []
    names = [(r.get("name") or "").strip() for r in _csv_rows(d / "reach.csv")]
    blank = sum(1 for n in names if not n)
    if blank:
        errors.append(f"{blank} reach(es) have an empty name")
    name_dups = {n: c for n, c in Counter(n for n in names if n).items() if c > 1}
    if name_dups:
        errors.append(f"duplicate reach.name values: {name_dups}")
    return errors


def _check_cross_set(d: Path) -> list[str]:
    """Child reach ids + JSON snapshot keys are subsets of reach.csv ids; every
    reach carries geometry."""
    errors: list[str] = []
    reach_ids = _id_set(d / "reach.csv", "id")
    for child in _REACH_CHILDREN:
        if (d / child).is_file():
            orphans = _id_set(d / child, "reach_id") - reach_ids
            if orphans:
                errors.append(f"{child} references reach ids not in reach.csv: {sorted(orphans)}")
    geom_ids: set[int] = set()
    for jname in (_GEOM_JSON, _GRADIENT_JSON):
        if not (d / jname).is_file():
            continue
        jkeys = {int(k) for k in json.loads((d / jname).read_text())}
        if jname == _GEOM_JSON:
            geom_ids = jkeys
        if jkeys - reach_ids:
            errors.append(f"{jname} has reach ids not in reach.csv: {sorted(jkeys - reach_ids)}")
    missing_geom = reach_ids - geom_ids
    if missing_geom:
        errors.append(f"reaches with no geometry in {_GEOM_JSON}: {sorted(missing_geom)}")
    return errors


def _check_reaches_on_materialized(dataset_dir: Path) -> list[str]:
    """Load the dataset into a throwaway file DB and run scan_for_issues by URL."""
    from sqlalchemy import create_engine

    from kayak.cli.check_reaches import scan_for_issues
    from kayak.db import metadata_csv as mc
    from kayak.db.models import Base

    out: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "validate.db"
        eng = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(eng)
        eng.dispose()
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:
                mc.upsert_csvs(conn, dataset_dir)
                _apply_geom_json(conn, dataset_dir)
            fk = conn.execute("PRAGMA foreign_key_check").fetchall()
            if fk:
                out.append(f"foreign-key violations after load: {fk[:10]}")
        except sqlite3.Error as exc:
            out.append(f"dataset failed to load into a fresh schema: {exc}")
            conn.close()
            return out
        finally:
            conn.close()
        _total, flagged = scan_for_issues(database_url=f"sqlite:///{db_path}")
        for label, issues in flagged:
            out.append(f"check-reaches: {label}: " + "; ".join(issues))
    return out


def _apply_geom_json(conn: sqlite3.Connection, dataset_dir: Path) -> None:
    """Apply reaches.json geom so check-reaches sees real geometry."""
    geom_path = dataset_dir / _GEOM_JSON
    if not geom_path.is_file():
        return
    for rid, geom in json.loads(geom_path.read_text()).items():
        conn.execute("UPDATE reach SET geom = ? WHERE id = ?", (geom, int(rid)))


def _main(args: argparse.Namespace) -> int:
    dataset_dir = Path(args.dir)
    if not dataset_dir.is_dir():
        print(f"validate-dataset: not a directory: {dataset_dir}")
        return 2
    errors = validate_dataset(dataset_dir)
    if errors:
        print(f"dataset validation FAILED ({dataset_dir}):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"dataset validation OK ({dataset_dir})")
    return 0
