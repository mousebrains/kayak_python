"""``levels validate-dataset <dir>`` — one gate for every dataset invariant.

Consolidates the checks that were scattered across the code repo's
``tests/test_id_counters.py``, ``test_reach_names.py``,
``test_committed_reach_geom.py`` and the data repo's stdlib ``validate.py``
into a single command the engine owns. The code repo runs it against the
fixture (``tests/fixtures/dataset``); the data repo's CI runs it against the
real dataset (S4b). Both get the same authoritative gate, so the two repos
can never drift on what "a valid dataset" means.

The file/column/id-bearing contract comes from :mod:`kayak.dataset.layout`,
the shared descriptor (S6 promotes it to the versioned contract manifest).
The command takes a REQUIRED explicit directory and never consults
``METADATA_DIR``/``DATASET_DIR``, so S6's root rename causes no validator
churn.

Checks are crash-safe: a malformed header, a non-integer id, or unparseable
JSON yields a focused error and skips the dependent checks rather than raising.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

from kayak.dataset import layout


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


def _read_json(path: Path) -> dict:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: expected a JSON object")
    return data


def validate_dataset(dataset_dir: Path) -> list[str]:
    """Return a list of human-readable problems; empty means the dataset is valid."""
    d = dataset_dir

    # (1) required files present.
    errors = _check_required_files(d)
    if errors:
        return errors  # nothing else is meaningful without the core files

    # (2) every CSV parses, is a known table, and has exactly the expected
    #     column set. Later checks only run for files that pass here.
    parse_errors, good_csvs = _check_csv_shape(d)
    errors.extend(parse_errors)

    # (3) id-counter coverage + invariants.
    errors.extend(_check_id_counters(d, good_csvs))
    # (4) reach names (only if reach.csv parsed cleanly).
    if "reach" in good_csvs:
        errors.extend(_check_reach_names(d))
        # (5) cross-set integrity.
        errors.extend(_check_cross_set(d, good_csvs))
    # (6) materialize + check-reaches (geometry AND gradient profile).
    errors.extend(_check_reaches_on_materialized(d))
    return errors


def _check_required_files(d: Path) -> list[str]:
    missing = [
        f"missing required file: {t}.csv"
        for t in layout.REQUIRED_CSVS
        if not (d / f"{t}.csv").is_file()
    ]
    if not (d / layout.ID_COUNTERS_CSV).is_file():
        missing.append(f"missing required file: {layout.ID_COUNTERS_CSV}")
    if not (d / layout.GEOM_JSON).is_file():
        missing.append(f"missing required file: {layout.GEOM_JSON}")
    return missing


def _check_csv_shape(d: Path) -> tuple[list[str], set[str]]:
    """Each *.csv parses, names a known table, and carries exactly the expected
    columns. Returns (errors, tables-that-passed)."""
    errors: list[str] = []
    good: set[str] = set()
    for csv_path in sorted(d.glob("*.csv")):
        table = csv_path.stem
        if table == "id_counters":
            header = _safe_header(csv_path, errors)
            if header is not None and set(header) != {"table", "next_id"}:
                errors.append(f"id_counters.csv: header must be table,next_id (got {header})")
            continue
        if table not in layout.KNOWN_CSVS:
            errors.append(f"unexpected CSV (not a known dataset table): {csv_path.name}")
            continue
        header = _safe_header(csv_path, errors)
        if header is None:
            continue
        expected = layout.expected_columns(table)
        got = set(header)
        if got != expected:
            missing = expected - got
            extra = got - expected
            parts = []
            if missing:
                parts.append(f"missing {sorted(missing)}")
            if extra:
                parts.append(f"unexpected {sorted(extra)}")
            errors.append(f"{csv_path.name}: column mismatch ({'; '.join(parts)})")
            continue
        good.add(table)
    return errors, good


def _safe_header(csv_path: Path, errors: list[str]) -> list[str] | None:
    try:
        with csv_path.open(encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if header is None:
                errors.append(f"{csv_path.name}: empty / headerless")
                return None
            for _ in reader:  # force a full parse
                pass
            return header
    except (OSError, csv.Error) as exc:
        errors.append(f"{csv_path.name}: unreadable ({exc})")
        return None


def _int_ids(rows: list[dict[str, str]], column: str) -> tuple[list[int], str | None]:
    """Parse an int column; return (ids, error-or-None) — never raises."""
    out: list[int] = []
    for r in rows:
        raw = (r.get(column) or "").strip()
        if not raw:
            continue
        try:
            out.append(int(raw))
        except ValueError:
            return out, f"non-integer {column}={raw!r}"
    return out, None


def _check_id_counters(d: Path, good_csvs: set[str]) -> list[str]:
    """Exactly one counter per present id-bearing table; unique ids; next_id
    strictly above the max existing id; no counters for absent/non-id tables."""
    errors: list[str] = []
    counters: dict[str, str] = {}
    for r in _csv_rows(d / layout.ID_COUNTERS_CSV):
        table = (r.get("table") or "").strip()
        if not table:
            continue
        if table in counters:
            errors.append(f"id_counters.csv: duplicate counter for {table}")
        counters[table] = (r.get("next_id") or "").strip()

    id_tables = layout.id_bearing_tables()
    present = {t for t in id_tables if (d / f"{t}.csv").is_file()}
    for table in sorted(present - set(counters)):
        errors.append(f"id_counters.csv: missing counter for id-bearing table {table}")
    for table in sorted(set(counters) - present):
        why = "not an id-bearing table" if table not in id_tables else f"{table}.csv absent"
        errors.append(f"id_counters.csv: counter for {table} but {why}")
    for table in sorted(present & set(counters)):
        errors.extend(_check_one_counter(d, table, counters[table]))
    return errors


def _check_one_counter(d: Path, table: str, next_raw: str) -> list[str]:
    """Unique ids and next_id > max for a single id-bearing table."""
    try:
        nxt = int(next_raw)
    except ValueError:
        return [f"id_counters.csv: non-integer next_id for {table}={next_raw!r}"]
    ids, err = _int_ids(_csv_rows(d / f"{table}.csv"), "id")
    if err:
        return [f"{table}.csv: {err}"]
    if not ids:
        return [f"{table}.csv (in id_counters) has no id rows"]
    errors: list[str] = []
    dups = sorted(i for i, c in Counter(ids).items() if c > 1)
    if dups:
        errors.append(f"{table}.csv has duplicate ids: {dups}")
    if max(ids) >= nxt:
        errors.append(
            f"{table}: next_id={nxt} <= max id {max(ids)} (stale counter / id-reuse risk)"
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


def _check_cross_set(d: Path, good_csvs: set[str]) -> list[str]:
    """Child reach ids + JSON snapshot keys are subsets of reach.csv ids; every
    reach carries geometry."""
    errors: list[str] = []
    reach_ids, err = _int_ids(_csv_rows(d / "reach.csv"), "id")
    if err:
        return [f"reach.csv: {err}"]
    reach_id_set = set(reach_ids)
    for child in layout.REACH_CHILD_CSVS:
        errors.extend(_check_child_reach_ids(d, child, reach_id_set))
    geom_ids, json_errors = _check_json_reach_keys(d, reach_id_set)
    errors.extend(json_errors)
    missing_geom = reach_id_set - geom_ids
    if missing_geom:
        errors.append(f"reaches with no geometry in {layout.GEOM_JSON}: {sorted(missing_geom)}")
    return errors


def _check_child_reach_ids(d: Path, child: str, reach_id_set: set[int]) -> list[str]:
    cpath = d / f"{child}.csv"
    if not cpath.is_file():
        return []
    child_ids, err = _int_ids(_csv_rows(cpath), "reach_id")
    if err:
        return [f"{child}.csv: {err}"]
    orphans = set(child_ids) - reach_id_set
    return (
        [f"{child}.csv references reach ids not in reach.csv: {sorted(orphans)}"] if orphans else []
    )


def _check_json_reach_keys(d: Path, reach_id_set: set[int]) -> tuple[set[int], list[str]]:
    """Return (geom-ids, errors) — JSON sidecar keys must be a subset of reach ids."""
    errors: list[str] = []
    geom_ids: set[int] = set()
    for jname in (layout.GEOM_JSON, layout.GRADIENT_JSON):
        if not (d / jname).is_file():
            continue
        try:
            keys = {int(k) for k in _read_json(d / jname)}
        except (ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{jname}: not a valid reach-id-keyed JSON object ({exc})")
            continue
        if jname == layout.GEOM_JSON:
            geom_ids = keys
        if keys - reach_id_set:
            errors.append(f"{jname} has reach ids not in reach.csv: {sorted(keys - reach_id_set)}")
    return geom_ids, errors


def _check_reaches_on_materialized(dataset_dir: Path) -> list[str]:
    """Load the dataset into a throwaway file DB and run scan_for_issues by URL.

    Applies BOTH the geometry and the gradient-profile snapshots so the
    extreme-peak / malformed-profile checks in check_reaches see real data
    rather than NULL.
    """
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
                _apply_reach_json(conn, dataset_dir, layout.GEOM_JSON, "geom")
                _apply_reach_json(conn, dataset_dir, layout.GRADIENT_JSON, "gradient_profile")
            fk = conn.execute("PRAGMA foreign_key_check").fetchall()
            if fk:
                out.append(f"foreign-key violations after load: {fk[:10]}")
        except (sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
            out.append(f"dataset failed to load into a fresh schema: {exc}")
            conn.close()
            return out
        finally:
            conn.close()
        _total, flagged = scan_for_issues(database_url=f"sqlite:///{db_path}")
        for label, issues in flagged:
            out.append(f"check-reaches: {label}: " + "; ".join(issues))
    return out


def _apply_reach_json(conn: sqlite3.Connection, dataset_dir: Path, jname: str, column: str) -> None:
    """Apply a reach-id-keyed JSON sidecar (geom / gradient_profile) to the DB."""
    jpath = dataset_dir / jname
    if not jpath.is_file():
        return
    for rid, value in _read_json(jpath).items():
        conn.execute(f"UPDATE reach SET {column} = ? WHERE id = ?", (value, int(rid)))


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
