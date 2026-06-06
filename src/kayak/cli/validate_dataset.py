"""``levels validate-dataset <dir>`` — one gate for every dataset invariant.

Consolidates the checks that were scattered across the code repo's
``tests/test_id_counters.py``, ``test_reach_names.py``,
``test_committed_reach_geom.py`` and the data repo's stdlib ``validate.py``
into a single command the engine owns. The code repo runs it against the
fixture (``tests/fixtures/dataset``); the data repo's CI runs it against the
real dataset (S4b). Both get the same authoritative gate, so the two repos
can never drift on what "a valid dataset" means.

The file/column/type/id-bearing contract comes from :mod:`kayak.dataset.layout`,
the shared descriptor (S6 promotes it to the versioned contract manifest). A
dataset is a **complete projection**: every contract CSV and both JSON sidecars
must be present (header-only / ``{}`` when empty), so a missing file is reported
as corruption rather than silently accepted as "not applicable". The command
takes a REQUIRED explicit directory and never consults
``METADATA_DIR``/``DATASET_DIR``, so S6's root rename causes no validator churn.

Checks are crash-safe: a malformed header, a non-integer id, a wrong-typed
value, or unparseable JSON yields a focused error and skips the dependent
checks rather than raising.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TypeGuard

from kayak.dataset import layout

# Cap per-file value errors so one badly-typed column can't flood the report.
_MAX_VALUE_ERRORS = 20

# A stable id (the PK or an FK to one) must be a positive canonical decimal
# integer: no leading zero, no sign — so base-62 handles stay 1-based and a
# row can't be addressed two ways ("1" vs "01").
_CANONICAL_ID = re.compile(r"^[1-9][0-9]*$")

# Explicit ASCII lexical grammars. Python's int()/float()/Decimal() accept
# spellings SQLite does not store as the declared type (underscores like "1_0",
# and for ints, values outside the signed-64-bit range stored with REAL
# affinity), so a value must match the grammar AND fit the storage domain.
_INT_RE = re.compile(r"^-?[0-9]+$")
_FLOAT_RE = re.compile(r"^-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?$")
_DECIMAL_RE = re.compile(r"^-?[0-9]+(?:\.[0-9]+)?$")  # fixed-point (coordinates)
# DateTime as exported (SQLAlchemy/SQLite text): "YYYY-MM-DD[ T]HH:MM:SS[.ffffff]".
# A grammar is needed because fromisoformat() also accepts compact forms like
# "20240101" that SQLite would store with INTEGER affinity.
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?$")
_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1
_INT64_MAX_DIGITS = 19  # 9223372036854775807 — guards int() before Python's 4300-digit limit

# id_counters.csv is a typed two-column contract file.
_ID_COUNTER_SPECS = {
    "table": layout.ColumnSpec("table", "text", nullable=False),
    "next_id": layout.ColumnSpec("next_id", "int", nullable=False),
}


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

    # (1) the complete projection is present.
    errors = _check_required_files(d)
    if errors:
        return errors  # nothing else is meaningful without the core files

    # (1b) every file decodes as UTF-8 — gate up front so no downstream reader
    #      hits an uncaught UnicodeDecodeError mid-validation.
    errors = _check_readable(d)
    if errors:
        return errors

    # (2) every CSV parses, is a known table, has unique headers and exactly the
    #     expected column set. Later checks only run for files that pass here.
    parse_errors, good_csvs = _check_csv_shape(d)
    errors.extend(parse_errors)

    # (3) every value has the right type, length, range and nullability.
    errors.extend(_check_csv_values(d, good_csvs))
    errors.extend(_check_one_csv_values(d / layout.ID_COUNTERS_CSV, _ID_COUNTER_SPECS))
    # (4) no duplicate primary keys on composite/natural-key tables (single-id
    #     dups are caught by the id-counter check).
    errors.extend(_check_duplicate_pks(d, good_csvs))
    # (5) id-counter coverage + invariants.
    errors.extend(_check_id_counters(d))
    # (6) reach names (only if reach.csv parsed cleanly).
    if "reach" in good_csvs:
        errors.extend(_check_reach_names(d))
        # (7) cross-set integrity.
        errors.extend(_check_cross_set(d))
    # (7b) gradient sidecar: validate the JSON encoded inside each profile string.
    errors.extend(_check_gradient_profiles(d))
    # (8) materialize + check-reaches — only when the dataset is otherwise clean.
    #     A wrong-typed value (e.g. a non-ISO datetime) would otherwise be loaded
    #     and crash SQLAlchemy's decoder mid-scan; the errors above already
    #     explain the problem, so there is nothing to gain by materializing.
    if not errors:
        errors.extend(_check_reaches_on_materialized(d))
    return errors


def _check_readable(d: Path) -> list[str]:
    """Every CSV / JSON sidecar decodes as UTF-8 (an invalid byte is corruption).

    Run before any structural check so a downstream reader never raises an
    uncaught UnicodeDecodeError — the module's crash-safe contract.
    """
    errors: list[str] = []
    targets = [*sorted(d.glob("*.csv")), d / layout.GEOM_JSON, d / layout.GRADIENT_JSON]
    for p in targets:
        if not p.is_file():
            continue
        try:
            p.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"{p.name}: unreadable / not valid UTF-8 ({exc})")
    return errors


def _check_required_files(d: Path) -> list[str]:
    """Every contract CSV, id_counters, and both JSON sidecars must be present —
    a dataset is the full export, so a missing file is corruption, not omission."""
    missing = [
        f"missing required file: {t}.csv"
        for t in layout.CONTRACT_CSVS
        if not (d / f"{t}.csv").is_file()
    ]
    for fname in (layout.ID_COUNTERS_CSV, layout.GEOM_JSON, layout.GRADIENT_JSON):
        if not (d / fname).is_file():
            missing.append(f"missing required file: {fname}")
    return missing


def _check_csv_shape(d: Path) -> tuple[list[str], set[str]]:
    """Each *.csv parses, names a known table, has no duplicate header names, and
    carries exactly the expected columns. Returns (errors, tables-that-passed)."""
    errors: list[str] = []
    good: set[str] = set()
    for csv_path in sorted(d.glob("*.csv")):
        table = csv_path.stem
        header = _safe_header(csv_path, errors)
        if header is None:
            continue
        dups = sorted({h for h, c in Counter(header).items() if c > 1})
        if dups:
            errors.append(f"{csv_path.name}: duplicate column names {dups}")
            continue
        if table == "id_counters":
            if set(header) != {"table", "next_id"}:
                errors.append(f"id_counters.csv: header must be table,next_id (got {header})")
            continue
        if table not in layout.KNOWN_CSVS:
            errors.append(f"unexpected CSV (not a known dataset table): {csv_path.name}")
            continue
        expected = layout.expected_columns(table)
        got = set(header)
        if got != expected:
            parts = []
            if expected - got:
                parts.append(f"missing {sorted(expected - got)}")
            if got - expected:
                parts.append(f"unexpected {sorted(got - expected)}")
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
    except (OSError, csv.Error, UnicodeError) as exc:
        errors.append(f"{csv_path.name}: unreadable ({exc})")
        return None


def _check_csv_values(d: Path, good_csvs: set[str]) -> list[str]:
    """Per-row width + per-cell type/nullability for every cleanly-shaped CSV.

    SQLite's dynamic typing would store a non-numeric value in a REAL column
    without complaint, so the materialized load can't catch a wrong-typed value
    — this check does, with file/row/column granularity.
    """
    errors: list[str] = []
    for table in sorted(good_csvs):
        specs = {s.name: s for s in layout.column_specs(table)}
        errors.extend(_check_one_csv_values(d / f"{table}.csv", specs))
    return errors


def _check_one_csv_values(csv_path: Path, specs: dict[str, layout.ColumnSpec]) -> list[str]:
    errors: list[str] = []
    with csv_path.open(encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None:
            return errors
        for n, row in enumerate(reader, start=2):  # row 1 is the header
            if len(row) != len(header):
                errors.append(
                    f"{csv_path.name} row {n}: has {len(row)} fields, expected {len(header)}"
                )
            for col, raw in zip(header, row, strict=False):  # width already checked above
                problem = _value_problem(specs.get(col), raw)
                if problem:
                    errors.append(f"{csv_path.name} row {n} col {col}: {problem}")
            if len(errors) >= _MAX_VALUE_ERRORS:
                errors.append(f"{csv_path.name}: ... further value errors suppressed")
                return errors
    return errors


def _value_problem(spec: layout.ColumnSpec | None, raw: str) -> str | None:
    """Return a problem string for a single cell, or None if it is acceptable."""
    if spec is None:
        return None  # unknown column already flagged by the shape check
    if raw == "":  # the loader maps "" -> NULL
        return None if spec.nullable else "empty value in NOT NULL column"
    return _KIND_CHECKS.get(spec.kind, _text_problem)(spec, raw)


def _int_problem(spec: layout.ColumnSpec, raw: str) -> str | None:
    if spec.is_id:
        if not _CANONICAL_ID.match(raw):
            return f"expected a positive integer id, got {raw!r}"
    elif not _INT_RE.match(raw):  # strict ASCII grammar — no underscores / '+'
        return f"expected integer, got {raw!r}"
    if _int64_overflow(raw):
        return f"integer out of SQLite 64-bit range, got {_trunc(raw)}"
    return None


def _int64_overflow(raw: str) -> bool:
    """True if a grammar-matched integer token is outside SQLite's signed 64-bit
    range. Insignificant leading zeros are stripped first (SQLite normalizes
    "007" -> 7), and the digit count is checked before ``int()`` so a
    pathologically long token never hits Python's 4300-digit conversion limit."""
    neg = raw.startswith("-")
    digits = (raw[1:] if neg else raw).lstrip("0") or "0"
    if len(digits) > _INT64_MAX_DIGITS:
        return True
    value = -int(digits) if neg else int(digits)
    return not (_INT64_MIN <= value <= _INT64_MAX)


def _trunc(raw: str, limit: int = 24) -> str:
    """Bounded repr for diagnostics — a 5,000-digit token must not flood output."""
    return repr(raw) if len(raw) <= limit else f"{raw[:limit]!r}... ({len(raw)} chars)"


def _bool_problem(spec: layout.ColumnSpec, raw: str) -> str | None:
    return None if raw in ("0", "1") else f"expected boolean 0/1, got {raw!r}"


def _datetime_problem(spec: layout.ColumnSpec, raw: str) -> str | None:
    if not _DATETIME_RE.match(raw):  # compact forms like "20240101" store as INTEGER
        return f"expected ISO datetime (YYYY-MM-DD HH:MM:SS), got {raw!r}"
    return None if _parses(datetime.fromisoformat, raw) else f"invalid datetime, got {raw!r}"


def _date_problem(spec: layout.ColumnSpec, raw: str) -> str | None:
    return None if _parses(date.fromisoformat, raw) else f"expected ISO date, got {raw!r}"


def _enum_problem(spec: layout.ColumnSpec, raw: str) -> str | None:
    return None if raw in spec.enums else f"expected one of {list(spec.enums)}, got {raw!r}"


def _text_problem(spec: layout.ColumnSpec, raw: str) -> str | None:
    if spec.max_length is not None and len(raw) > spec.max_length:
        return f"exceeds max length {spec.max_length} (got {len(raw)} chars)"
    return None


def _number_problem(spec: layout.ColumnSpec, raw: str) -> str | None:
    if spec.decimal_spec is not None:
        if not _DECIMAL_RE.match(raw):  # fixed-point ASCII grammar (no '_', no exp)
            return f"not a valid decimal, got {raw!r}"
        problem = _decimal_problem(raw, *spec.decimal_spec)
        if problem:
            return problem
    else:
        if not _FLOAT_RE.match(raw):  # strict float grammar — no underscores
            return f"expected a number, got {raw!r}"
        if not math.isfinite(float(raw)):
            return f"expected a finite number, got {raw!r}"
    if spec.value_range is not None:
        lo, hi = spec.value_range
        if not (lo <= float(raw) <= hi):
            return f"out of range [{lo}, {hi}], got {raw!r}"
    return None


def _decimal_problem(raw: str, precision: int, scale: int) -> str | None:
    """Enforce a ``Numeric(precision, scale)`` contract on a decimal string.

    The caller has already matched ``raw`` against the fixed-point grammar, so
    ``Decimal`` cannot raise here.
    """
    d = Decimal(raw)
    if not d.is_finite():
        return f"expected a finite number, got {raw!r}"
    exponent = d.as_tuple().exponent
    assert isinstance(exponent, int)  # a finite Decimal always has an int exponent
    frac = max(0, -exponent)
    if frac > scale:
        return f"{frac} decimal places exceeds scale {scale}, got {raw!r}"
    int_digits = max(0, d.adjusted() + 1) if d != 0 else 0
    if int_digits > precision - scale:
        return f"{int_digits} integer digits exceeds precision {precision} (scale {scale}), got {raw!r}"
    return None


# Per-kind value checkers; unknown kinds (plain text) fall back to _text_problem.
_KIND_CHECKS: dict[str, Callable[[layout.ColumnSpec, str], str | None]] = {
    "int": _int_problem,
    "number": _number_problem,
    "bool": _bool_problem,
    "datetime": _datetime_problem,
    "date": _date_problem,
    "enum": _enum_problem,
    "text": _text_problem,
}


def _parses(fn: Callable[[str], object], raw: str) -> bool:
    try:
        fn(raw)
        return True
    except (ValueError, TypeError):
        return False


def _check_duplicate_pks(d: Path, good_csvs: set[str]) -> list[str]:
    """Reject duplicate primary-key tuples on composite/natural-key tables.

    SQLite's upsert silently collapses a duplicate-PK insert, so two identical
    ``gauge_source`` rows or two ``class_description`` rows with the same name
    would load as one. Single-``id`` tables are covered by the id-counter check.
    """
    errors: list[str] = []
    for table in sorted(good_csvs):
        pk = layout.primary_key_columns(table)
        if pk == ["id"]:
            continue
        specs = {s.name: s for s in layout.column_specs(table)}
        seen: Counter[tuple[object, ...]] = Counter()
        for r in _csv_rows(d / f"{table}.csv"):
            seen[tuple(_norm_pk_cell(specs.get(c), r.get(c) or "") for c in pk)] += 1
        dups = sorted(str(k) for k, c in seen.items() if c > 1)
        if dups:
            errors.append(f"{table}.csv has duplicate primary keys {pk}: {dups}")
    return errors


def _norm_pk_cell(spec: layout.ColumnSpec | None, raw: str) -> object:
    """Normalize a PK cell to its SQLite storage value so equivalent spellings
    collide: '1'/'01' as int; '1'/'1.0'/'1e0' as the same REAL. Others stay literal."""
    if spec is not None:
        try:
            if spec.kind == "int":
                return int(raw)
            if spec.kind == "number":
                return float(raw)
        except ValueError:
            return raw
    return raw


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


def _check_id_counters(d: Path) -> list[str]:
    """Exactly one counter per id-bearing contract table; unique ids; next_id
    strictly above the max existing id; no counters for non-id tables."""
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
    for table in sorted(id_tables - set(counters)):
        errors.append(f"id_counters.csv: missing counter for id-bearing table {table}")
    for table in sorted(set(counters) - id_tables):
        errors.append(f"id_counters.csv: counter for {table} but it is not an id-bearing table")
    for table in sorted(id_tables & set(counters)):
        errors.extend(_check_one_counter(d, table, counters[table]))
    return errors


def _check_one_counter(d: Path, table: str, next_raw: str) -> list[str]:
    """Unique ids and next_id > max for a single id-bearing table (an empty
    table is fine — its counter just records the never-reused high-water mark)."""
    try:
        nxt = int(next_raw)
    except ValueError:
        return [f"id_counters.csv: non-integer next_id for {table}={next_raw!r}"]
    if nxt < 1:
        return [f"id_counters.csv: next_id for {table} must be >= 1 (got {nxt})"]
    ids, err = _int_ids(_csv_rows(d / f"{table}.csv"), "id")
    if err:
        return [f"{table}.csv: {err}"]
    errors: list[str] = []
    dups = sorted(i for i, c in Counter(ids).items() if c > 1)
    if dups:
        errors.append(f"{table}.csv has duplicate ids: {dups}")
    if ids and max(ids) >= nxt:
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


def _check_cross_set(d: Path) -> list[str]:
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
    if orphans:
        return [f"{child}.csv references reach ids not in reach.csv: {sorted(orphans)}"]
    return []


def _check_json_reach_keys(d: Path, reach_id_set: set[int]) -> tuple[set[int], list[str]]:
    """Return (geom-ids, errors) — JSON sidecar keys must be a subset of reach ids."""
    errors: list[str] = []
    geom_ids: set[int] = set()
    for jname in (layout.GEOM_JSON, layout.GRADIENT_JSON):
        if not (d / jname).is_file():
            continue
        keys, errs = _parse_reach_id_json(d / jname)
        errors.extend(errs)
        if jname == layout.GEOM_JSON:
            geom_ids = keys
        if keys - reach_id_set:
            errors.append(f"{jname} has reach ids not in reach.csv: {sorted(keys - reach_id_set)}")
    return geom_ids, errors


def _check_gradient_profiles(d: Path) -> list[str]:
    """Validate the JSON *inside* each gradient sidecar string.

    The sidecar value is itself a JSON-encoded profile. ``check-reaches`` treats
    any non-object as an empty sample set and the PHP renderer drops the chart
    for a missing/non-list ``samples``, so a wrong-shaped-but-valid profile would
    silently lose its gradient chart. Require the object/``samples``-list contract
    and numeric per-sample fields before materialization.
    """
    path = d / layout.GRADIENT_JSON
    if not path.is_file():
        return []
    try:
        outer = json.loads(path.read_text(), parse_constant=_reject_json_constant)
    except (ValueError, json.JSONDecodeError):
        return []  # outer-JSON problems already reported by _parse_reach_id_json
    if not isinstance(outer, dict):
        return []
    errors: list[str] = []
    for rid, raw in outer.items():
        if not isinstance(raw, str) or raw == "":
            continue  # empty/non-string already reported
        problem = _gradient_profile_problem(raw)
        if problem:
            errors.append(f"{layout.GRADIENT_JSON} reach {rid}: {problem}")
    return errors


def _gradient_profile_problem(raw: str) -> str | None:
    """The contract for a single decoded gradient profile (None == ok).

    Parses with recursive duplicate-key detection and NaN/Infinity rejection,
    then enforces the object/``samples``-list shape and each sample's domain
    (finite required numbers, positive ``w_mi``, ordered ``d_mi``, and typed
    optional fields) the SVG renderer + JS hydration assume.
    """
    try:
        prof = json.loads(
            raw, object_pairs_hook=_no_dup_pairs, parse_constant=_reject_json_constant
        )
    except (ValueError, json.JSONDecodeError) as exc:
        return f"profile is not valid JSON ({exc})"
    if not isinstance(prof, dict):
        return f"profile must be a JSON object, got {type(prof).__name__}"
    samples = prof.get("samples")
    if not isinstance(samples, list):
        return "profile 'samples' must be a list"
    prev_d: float | None = None
    for i, s in enumerate(samples):
        problem = _gradient_sample_problem(s, i, prev_d)
        if problem:
            return problem
        prev_d = s["d_mi"]  # validated finite above
    return None


def _gradient_sample_problem(s: object, i: int, prev_d: float | None) -> str | None:
    if not isinstance(s, dict):
        return f"sample {i} must be an object"
    for field in ("d_mi", "w_mi", "grad_ft_per_mi"):
        if not _finite_number(s.get(field)):
            return f"sample {i} field {field!r} must be a finite number"
    if s["w_mi"] <= 0:
        return f"sample {i} w_mi must be positive, got {s['w_mi']}"
    if prev_d is not None and not s["d_mi"] > prev_d:
        return f"sample {i} d_mi {s['d_mi']} <= previous {prev_d} (samples must be ordered)"
    return _gradient_optional_problem(s, i)


def _gradient_optional_problem(s: dict[str, object], i: int) -> str | None:
    if "significant" in s and not isinstance(s["significant"], bool):
        return f"sample {i} 'significant' must be a boolean"
    for coord, (lo, hi) in (("lat", (-90.0, 90.0)), ("lon", (-180.0, 180.0))):
        v = s.get(coord)
        if coord not in s or v is None:
            continue
        if not _finite_number(v):
            return f"sample {i} {coord!r} must be a finite number or null"
        if not lo <= v <= hi:
            return f"sample {i} {coord!r} out of range [{lo}, {hi}], got {v}"
    return None


def _finite_number(v: object) -> TypeGuard[float]:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _no_dup_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """object_pairs_hook that rejects duplicate keys at any depth (else
    json.loads keeps the last value silently)."""
    seen: set[str] = set()
    for k, _ in pairs:
        if k in seen:
            raise ValueError(f"duplicate key {k!r}")
        seen.add(k)
    return dict(pairs)


def _reject_json_constant(name: str) -> object:
    # parse_constant fires for NaN / Infinity / -Infinity — non-standard JSON
    # that json.loads accepts by default and that would store as NULL geometry.
    raise ValueError(f"non-standard JSON constant {name!r}")


def _parse_reach_id_json(path: Path) -> tuple[set[int], list[str]]:
    """Parse a reach-id-keyed JSON object, returning (ids-with-usable-geometry,
    errors).

    Beyond duplicate-key detection, every key must be a positive-canonical id
    (``"01"``/``"+1"``/``" 1"`` rejected on their own, not only on collision) and
    every present value must be a non-empty string — a ``null``/``""``/non-string
    value would materialize as NULL/empty geometry that ``check-reaches`` treats
    as optional. Only keys with a usable string value are returned as geometry
    ids, so the "every reach has geometry" check is value-based, not key-based.
    """
    errors: list[str] = []
    captured: list[list[tuple[str, object]]] = []

    def hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        captured.append(pairs)
        return dict(pairs)

    try:
        json.loads(path.read_text(), object_pairs_hook=hook, parse_constant=_reject_json_constant)
    except (ValueError, json.JSONDecodeError) as exc:
        return set(), [f"{path.name}: invalid JSON ({exc})"]
    if not captured:
        return set(), [f"{path.name}: expected a JSON object"]
    top = captured[-1]  # the outermost object is completed last
    raw_dups = sorted({k for k, c in Counter(k for k, _ in top).items() if c > 1})
    if raw_dups:
        errors.append(f"{path.name}: duplicate keys {raw_dups}")
    usable: set[int] = set()
    for k, v in top:
        if not _CANONICAL_ID.match(k):
            errors.append(f"{path.name}: non-canonical reach-id key {_trunc(k)}")
            continue
        if _int64_overflow(k):
            errors.append(f"{path.name}: reach-id key out of SQLite 64-bit range {_trunc(k)}")
            continue
        if not isinstance(v, str) or v == "":
            errors.append(f"{path.name}: reach {k} has an empty / non-string value")
            continue
        usable.add(int(k))
    return usable, errors


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
        try:
            _total, flagged = scan_for_issues(database_url=f"sqlite:///{db_path}")
        except Exception as exc:  # defensive backstop — never crash the validator
            return [f"check-reaches scan failed: {exc}"]
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
