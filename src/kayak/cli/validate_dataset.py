"""``levels validate-dataset <dir>`` — one gate for every dataset invariant.

Consolidates the dataset-integrity checks the code repo previously kept as
standalone tests reading the real dataset — ``test_id_counters.py``,
``test_reach_names.py``, ``test_committed_reach_geom.py``, and the USGS
station-id check from ``test_fetch_usgs_ogc.py`` (all removed in #124) — plus
the data repo's stdlib ``validate.py``, into a single command the engine owns.
The code repo runs it against the fixture (``tests/fixtures/dataset``); the
data repo's CI runs it against the real dataset (S4b). Both get the same
authoritative gate, so the two repos can never drift on what "a valid dataset"
means.

Validation gates on the **dataset contract first** (S6.2): ``dataset.yaml`` must
declare a ``contract_version`` the engine supports (a dataset with none is
"contract 0" and is rejected) — see :mod:`kayak.dataset.contract`. Only then are
the content checks run. The file/column/type/id-bearing contract comes from
:mod:`kayak.dataset.layout`, the shared descriptor. A dataset is a **complete
projection**: every contract CSV, both JSON sidecars, and ``retired_ids.yaml``
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

from kayak.dataset import assets as dataset_assets
from kayak.dataset import contract, layout, region, site
from kayak.dataset import map as dataset_map  # `map` shadows the builtin → alias

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
_GUIDEBOOK_SHORT_LABEL_RE = re.compile(r"^[A-Z][A-Z0-9]{0,15}$")
# DateTime as exported (SQLAlchemy/SQLite text): "YYYY-MM-DD[ T]HH:MM:SS[.ffffff]".
# A grammar is needed because fromisoformat() also accepts compact forms like
# "20240101" that SQLite would store with INTEGER affinity. Fractional seconds
# are capped at 6 digits — Python's datetime keeps only microseconds, so a longer
# fraction cannot survive a DB/export round trip unchanged.
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?$")
_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1
_INT64_MAX_DIGITS = 19  # 9223372036854775807 — guards int() before Python's 4300-digit limit

# id_counters.csv is a typed two-column contract file.
_ID_COUNTER_SPECS = {
    "table": layout.ColumnSpec("table", "text", nullable=False),
    "next_id": layout.ColumnSpec("next_id", "int", nullable=False),
}

# Regression report content (S2): published md/svg/json under <dataset>/regression/,
# linked from the site by calc_expression.provenance_slug.
_REGRESSION_DIR = "regression"
_REGRESSION_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")  # matches gauge_detail.php's slug check
# ](./X.ext) — matches both `[text](./X)` links and `![alt](./X)` image embeds.
_REGRESSION_REF_RE = re.compile(r"\]\(\./([A-Za-z0-9_-]+)\.(md|html|svg|json)\)")


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


def validate_dataset(dataset_dir: Path, warnings: list[str] | None = None) -> list[str]:
    """Return a list of human-readable problems; empty means the dataset is valid.

    Non-fatal advisories (e.g. an orphan regression report) are appended to
    ``warnings`` when a list is supplied; they never affect the returned error
    list, so existing callers/tests that ignore them are unchanged.
    """
    d = dataset_dir
    warn = warnings if warnings is not None else []

    # (0) the dataset contract — validate it before reading any content (S6.2).
    #     A missing dataset.yaml is "contract 0" and is rejected outright; an
    #     unreadable/out-of-range/invalid manifest fails here too. Gating first
    #     means a dataset the engine can't understand never reaches the content
    #     checks (or, in later slices, a mutating command).
    errors = _check_dataset_yaml(d)
    if errors:
        return errors

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
    # (5) id-counter coverage + invariants, including retired-id reuse/high-water.
    #     Retired ids degrade to {} on any shape error, so the counter check
    #     falls back to active-only rather than acting on half-trusted data.
    retired_errors, retired = _check_retired_ids(d)
    errors.extend(retired_errors)
    errors.extend(_check_id_counters(d, retired))
    # (5b) source-name wiring invariants (e.g. USGS station ids).
    errors.extend(_check_source_names(d, good_csvs))
    # (5b') state names must be safe — they become URL/filename/HTML in the build
    #       (S3b-2 made the served state set data-driven).
    errors.extend(_check_state_names(d, good_csvs))
    errors.extend(_check_state_references(d, good_csvs))
    errors.extend(_check_guidebook_short_labels(d, good_csvs))
    # (5c) source<->gauge cardinality (every source has exactly one gauge) + the
    #      gauge_source / reach.gauge_id FK references.
    errors.extend(_check_gauge_source(d, good_csvs))
    errors.extend(_check_reach_gauge(d, good_csvs))
    errors.extend(_check_fetch_url_policy(d, good_csvs))
    # (6) reach names (only if reach.csv parsed cleanly).
    if "reach" in good_csvs:
        errors.extend(_check_reach_names(d))
        # (7) cross-set integrity.
        errors.extend(_check_cross_set(d))
    # (7b) gradient sidecar: validate the JSON encoded inside each profile string.
    errors.extend(_check_gradient_profiles(d))
    # (7c) regression reports: every provenance_slug has its report + sidecars,
    #      referenced files exist, and the served content passes the security/shape
    #      gate. Orphan reports are advisory (appended to `warn`, never fatal).
    reg_errors, reg_warnings = _check_regression(d)
    errors.extend(reg_errors)
    warn.extend(reg_warnings)
    # (7d) site identity: an opt-in site.yaml must parse + pass the typed/safe-shape
    #      gate (the engine + PHP both render it into HTML). Absent → no error.
    errors.extend(_check_site_yaml(d))
    # (7e) region presentation: an opt-in region.yaml (per-state links + weather)
    #      must parse + pass the typed/safe-shape gate (links render into HTML).
    errors.extend(_check_region_yaml(d))
    # (7f) map config: an opt-in map.yaml (extent + overlay layers) must parse +
    #      pass the typed/safe-shape gate (values render into the map JS / hrefs and
    #      drive fetch-map-layers' ArcGIS fetches).
    errors.extend(_check_map_yaml(d))
    # (7g) site prose: site/*.md render clean; legal pages are required when the
    #      dataset is publishable.
    errors.extend(_check_site_prose(d))
    # (7h) site assets: optional public image overrides must match the typed
    #      allowlist before build copies them into /static.
    errors.extend(_check_site_assets(d))
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


def _check_dataset_yaml(d: Path) -> list[str]:
    """Validate the dataset contract (``dataset.yaml``) before any content (S6.2).

    A missing ``dataset.yaml`` is contract 0 — rejected with a message naming the
    engine's supported range and the remediation. A present manifest is parsed
    (corruption → error) and field-checked. Delegates to ``contract.check_contract``
    so the integrity gate (and its contract-0 message) lives in one place, shared
    with the production gate ``contract.gate_for_use`` (S6.4). ``validate-dataset``
    uses ``check_contract`` (not ``gate_for_use``): a ``scaffold`` dataset is still
    a *valid* dataset to validate — only production commands refuse it.
    """
    return contract.check_contract(d)


def _check_required_files(d: Path) -> list[str]:
    """Every contract CSV, id_counters, both JSON sidecars, and retired_ids.yaml
    must be present — a dataset is the full export, so a missing file is
    corruption, not omission."""
    missing = [
        f"missing required file: {t}.csv"
        for t in layout.CONTRACT_CSVS
        if not (d / f"{t}.csv").is_file()
    ]
    for fname in (
        layout.ID_COUNTERS_CSV,
        layout.GEOM_JSON,
        layout.GRADIENT_JSON,
        contract.RETIRED_IDS_YAML,
    ):
        if not (d / fname).is_file():
            missing.append(f"missing required file: {fname}")
    return missing


def _check_csv_shape(d: Path) -> tuple[list[str], set[str]]:
    """Each *.csv parses, names a known table, has no duplicate header names, and
    carries the expected columns (required ones present, no unexpected ones;
    :func:`layout.optional_columns` may be absent). Returns (errors,
    tables-that-passed)."""
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
        required = expected - layout.optional_columns(table)
        got = set(header)
        missing = required - got  # optional columns may be absent
        unexpected = got - expected
        if missing or unexpected:
            parts = []
            if missing:
                parts.append(f"missing {sorted(missing)}")
            if unexpected:
                parts.append(f"unexpected {sorted(unexpected)}")
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


def _bounded_int(raw: str) -> int | None:
    """``int(raw)`` within SQLite's signed 64-bit range, else None.

    Never raises and never feeds a pathologically long token to ``int()``
    (Python caps str->int at 4300 digits) — strips insignificant leading zeros
    and guards by digit count first.
    """
    if not _INT_RE.match(raw):
        return None
    neg = raw.startswith("-")
    digits = (raw[1:] if neg else raw).lstrip("0") or "0"
    if len(digits) > _INT64_MAX_DIGITS:
        return None
    value = -int(digits) if neg else int(digits)
    return value if _INT64_MIN <= value <= _INT64_MAX else None


def _int_ids(rows: list[dict[str, str]], column: str) -> tuple[list[int], str | None]:
    """Parse an int column; return (ids, error-or-None) — never raises, bounded."""
    out: list[int] = []
    for r in rows:
        raw = (r.get(column) or "").strip()
        if not raw:
            continue
        value = _bounded_int(raw)
        if value is None:
            return out, f"non-integer or out-of-range {column}={_trunc(raw)}"
        out.append(value)
    return out, None


def _check_retired_ids(d: Path) -> tuple[list[str], dict[str, set[int]]]:
    """Parse ``retired_ids.yaml`` → (errors, per-table retired-id sets).

    The file records purged ids so a deleted row's id is never reused and the id
    counter stays above it (see :func:`_check_one_counter`). Shape contract: each
    key is an id-bearing table; each value a list of unique positive ints within
    SQLite's signed-64-bit range (``bool`` is rejected — it is an ``int``
    subclass; YAML yields real ``int``s, so this is an isinstance + range test,
    not the CSV string path). On **any** shape failure the retired map degrades
    to ``{}`` so the counter check falls back to active-only rather than acting
    on half-trusted data — matching the module's crash-safe contract.
    """
    try:
        meta = contract.load_retired_ids(d)
    except ValueError as e:
        return [str(e)], {}
    if meta is None:  # absent — already reported by _check_required_files
        return [], {}
    id_tables = layout.id_bearing_tables()
    errors: list[str] = []
    retired: dict[str, set[int]] = {}
    for table, ids in meta.items():
        if not isinstance(table, str) or table not in id_tables:
            errors.append(
                f"{contract.RETIRED_IDS_YAML}: {_trunc(str(table))} is not an id-bearing table"
            )
            continue
        table_errs, parsed = _parse_retired_id_list(table, ids)
        errors.extend(table_errs)
        retired[table] = parsed
    if errors:
        return errors, {}
    return [], retired


def _parse_retired_id_list(table: str, ids: object) -> tuple[list[str], set[int]]:
    """One table's retired-id list: a list of unique positive 64-bit ints."""
    where = f"{contract.RETIRED_IDS_YAML} table {table}"
    if not isinstance(ids, list):
        return [f"{where}: value must be a list of ids"], set()
    errors: list[str] = []
    out: list[int] = []
    for item in ids:
        if not isinstance(item, int) or isinstance(item, bool):
            errors.append(f"{where}: retired id must be an integer, got {_trunc(str(item))}")
        elif not (1 <= item <= _INT64_MAX):
            errors.append(f"{where}: retired id out of 1..2^63-1, got {_trunc(str(item))}")
        else:
            out.append(item)
    dups = sorted(i for i, c in Counter(out).items() if c > 1)
    if dups:
        errors.append(f"{where}: duplicate retired ids {dups}")
    return errors, set(out)


def _check_id_counters(d: Path, retired: dict[str, set[int]]) -> list[str]:
    """Exactly one counter per id-bearing contract table; unique ids; next_id
    strictly above the max existing-or-retired id; no counters for non-id
    tables; no id both active and retired."""
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
        errors.extend(_check_one_counter(d, table, counters[table], retired.get(table, set())))
    return errors


def _check_one_counter(d: Path, table: str, next_raw: str, retired_ids: set[int]) -> list[str]:
    """Unique ids and next_id above the max active-or-retired id for a single
    id-bearing table (an empty table is fine — its counter just records the
    never-reused high-water mark). ``retired_ids`` are purged ids that stay
    reserved: they may not reappear as active rows, and the counter must stay
    above them too."""
    nxt = _bounded_int(next_raw)
    if nxt is None:
        return [f"id_counters.csv: invalid / out-of-range next_id for {table}={_trunc(next_raw)}"]
    if nxt < 1:
        return [f"id_counters.csv: next_id for {table} must be >= 1 (got {nxt})"]
    ids, err = _int_ids(_csv_rows(d / f"{table}.csv"), "id")
    if err:
        return [f"{table}.csv: {err}"]
    errors: list[str] = []
    dups = sorted(i for i, c in Counter(ids).items() if c > 1)
    if dups:
        errors.append(f"{table}.csv has duplicate ids: {dups}")
    reused = sorted(set(ids) & retired_ids)
    if reused:
        errors.append(f"{table}: id(s) {reused} are both active and retired")
    peak = max([*ids, *retired_ids], default=0)
    if peak >= nxt:
        errors.append(
            f"{table}: next_id={nxt} <= max id {peak} (active or retired) "
            "(stale counter / id-reuse risk)"
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


def _check_source_names(d: Path, good_csvs: set[str]) -> list[str]:
    """USGS sources must be named a bare numeric station id.

    The source-based USGS-OGC fetch keys on ``source.name`` as the station id
    (``kayak.cli.fetch_usgs_ogc``), so a non-numeric name would silently fetch
    the wrong station / nothing. Consolidated from the former
    ``tests/test_cli/test_fetch_usgs_ogc.py::test_usgs_source_names_are_station_ids``
    so the data repo's CI gates it via ``validate-dataset`` (S4b). ASCII digits
    only — ``str.isdigit()`` alone accepts non-ASCII digits (e.g. ``"٣"``) the
    fetch URL can't use, so it's paired with ``isascii()`` to match the rest of
    the validator's ASCII-strict numeric grammars.
    """
    if "source" not in good_csvs:
        return []
    offenders: list[str] = []
    for r in _csv_rows(d / "source.csv"):
        if (r.get("agency") or "").strip() != "USGS":
            continue
        name = (r.get("name") or "").strip()
        if not (name.isascii() and name.isdigit()):
            offenders.append(name)
    if offenders:
        return [f"source.csv: USGS source name must be a numeric station id, got {offenders}"]
    return []


def _source_gauge_map(gs_rows: list[dict[str, str]]) -> dict[int, set[int]]:
    """source_id -> set of distinct gauge_ids it links to (rows with an empty/invalid
    cell are skipped — those are reported by the value checks)."""
    out: dict[int, set[int]] = {}
    for r in gs_rows:
        sid = _bounded_int((r.get("source_id") or "").strip())
        gid = _bounded_int((r.get("gauge_id") or "").strip())
        if sid is not None and gid is not None:
            out.setdefault(sid, set()).add(gid)
    return out


def _check_fetch_url_policy(d: Path, good_csvs: set[str]) -> list[str]:
    """fetch_url.unknown_station_policy (optional column; S1) must be blank or one of
    layout.UNKNOWN_STATION_POLICIES. Blank/absent = the default reject. The runtime
    treats anything but 'ignore' as reject, but the dataset keeps the canonical set
    so a typo ('ingore') is caught here rather than silently demoted at fetch time.
    A fetch_url.csv with no such column (the common case) is fine — it's optional."""
    if "fetch_url" not in good_csvs:
        return []
    errors: list[str] = []
    for i, r in enumerate(_csv_rows(d / "fetch_url.csv")):
        raw = (r.get("unknown_station_policy") or "").strip()
        if raw and raw not in layout.UNKNOWN_STATION_POLICIES:
            allowed = ", ".join(repr(v) for v in layout.UNKNOWN_STATION_POLICIES)
            errors.append(
                f"fetch_url.csv[{i}]: unknown_station_policy must be blank or one of "
                f"{allowed} (got {_trunc(raw)})"
            )
    return errors


def _check_guidebook_short_labels(d: Path, good_csvs: set[str]) -> list[str]:
    """Optional guidebook.short_label values are compact public UI labels."""
    if "guidebook" not in good_csvs:
        return []
    errors: list[str] = []
    for i, row in enumerate(_csv_rows(d / "guidebook.csv")):
        raw = (row.get("short_label") or "").strip()
        if raw and not _GUIDEBOOK_SHORT_LABEL_RE.fullmatch(raw):
            errors.append(
                f"guidebook.csv[{i}]: short_label must match [A-Z][A-Z0-9]{{0,15}} when present"
            )
    return errors


def _check_gauge_source(d: Path, good_csvs: set[str]) -> list[str]:
    """Every source is linked to EXACTLY one gauge, and gauge_source's ids resolve.

    Domain invariant: every source must be associated with a gauge (source->gauge is
    1-to-1; gauge->source is 1-to-many). The pipeline orphan-check only flags
    fetch-active orphans, so enforce it here for ALL sources: no source without a
    gauge_source row, none linked to >1 gauge, and gauge_source's gauge_id/source_id
    reference real rows.
    """
    if not {"source", "gauge", "gauge_source"} <= good_csvs:
        return []
    source_ids, err = _int_ids(_csv_rows(d / "source.csv"), "id")
    if err:
        return [f"source.csv: {err}"]
    gauge_ids, err = _int_ids(_csv_rows(d / "gauge.csv"), "id")
    if err:
        return [f"gauge.csv: {err}"]
    gs_rows = _csv_rows(d / "gauge_source.csv")
    gs_sources, err = _int_ids(gs_rows, "source_id")
    if err:
        return [f"gauge_source.csv: {err}"]
    gs_gauges, err = _int_ids(gs_rows, "gauge_id")
    if err:
        return [f"gauge_source.csv: {err}"]
    errors: list[str] = []
    source_set, gauge_set = set(source_ids), set(gauge_ids)
    dangling_src = sorted(set(gs_sources) - source_set)
    if dangling_src:
        errors.append(f"gauge_source.csv references source ids not in source.csv: {dangling_src}")
    dangling_gauge = sorted(set(gs_gauges) - gauge_set)
    if dangling_gauge:
        errors.append(f"gauge_source.csv references gauge ids not in gauge.csv: {dangling_gauge}")
    # Count DISTINCT gauges per source — a same-gauge-twice row is a duplicate-PK
    # error (caught by _check_duplicate_pks), not a "more than one gauge" error, so
    # the set dedups it and the message below stays literally true.
    source_gauges = _source_gauge_map(gs_rows)
    orphans = sorted(sid for sid in source_set if not source_gauges.get(sid))
    if orphans:
        errors.append(
            f"source ids with no gauge_source row (every source needs a gauge): {orphans}"
        )
    doubled = sorted(sid for sid in source_set if len(source_gauges.get(sid, ())) > 1)
    if doubled:
        errors.append(f"source ids linked to more than one gauge: {doubled}")
    return errors


def _check_reach_gauge(d: Path, good_csvs: set[str]) -> list[str]:
    """A reach's optional ``gauge_id`` must reference an existing gauge (<=1 gauge per
    reach is already structural — it is a scalar column; NULL/empty = no gauge)."""
    if not {"reach", "gauge"} <= good_csvs:
        return []
    gauge_ids, err = _int_ids(_csv_rows(d / "gauge.csv"), "id")
    if err:
        return [f"gauge.csv: {err}"]
    reach_gauge_ids, err = _int_ids(_csv_rows(d / "reach.csv"), "gauge_id")
    if err:
        return [f"reach.csv: {err}"]
    dangling = sorted(set(reach_gauge_ids) - set(gauge_ids))
    if dangling:
        return [f"reach.csv references gauge ids not in gauge.csv: {dangling}"]
    return []


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
    for a missing/non-list ``samples``, so a wrong-shaped profile would silently
    lose its chart. Require the object/``samples``-list contract and each sample's
    integrity (finite + non-negative distance/gradient, positive ``w_mi``,
    ordered ``d_mi``, typed optional fields).

    Deliberately NOT coupled to ``reach.length``: gradient extent and the reach
    length legitimately diverge — a reservoir at the take-out yields no gradient
    data for the lower reach (renderer shows that span as zero gradient), and a
    trace can run slightly past the editorial length (renderer clips it). The
    renderer owns the x-domain; the validator owns gradient integrity.
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
    then enforces the object/``samples``-list shape and each sample's integrity
    (finite + non-negative required numbers, positive ``w_mi``, ordered ``d_mi``,
    and typed optional fields) the SVG renderer + JS hydration assume.
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
    vals: dict[str, float] = {}
    for field in ("d_mi", "w_mi", "grad_ft_per_mi"):
        v = s.get(field)
        if not _finite_number(v):
            return f"sample {i} field {field!r} must be a finite number"
        vals[field] = float(v)  # _finite_number narrows v to a real number
    return _gradient_sample_domain_problem(s, vals, i, prev_d)


def _gradient_sample_domain_problem(
    s: dict[str, object], vals: dict[str, float], i: int, prev_d: float | None
) -> str | None:
    d_mi, w_mi, grad = vals["d_mi"], vals["w_mi"], vals["grad_ft_per_mi"]
    if w_mi <= 0:
        return f"sample {i} w_mi must be positive, got {w_mi}"
    if d_mi < 0:
        return f"sample {i} d_mi must be non-negative, got {d_mi}"
    if grad < 0:
        return f"sample {i} grad_ft_per_mi must be non-negative, got {grad}"
    if prev_d is not None and not d_mi > prev_d:
        return f"sample {i} d_mi {d_mi} <= previous {prev_d} (samples must be ordered)"
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
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(float(v))  # float() of a huge int raises OverflowError
    except OverflowError:
        return False


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


def _regression_slugs(d: Path) -> tuple[set[str], list[str]]:
    """Non-empty ``calc_expression.provenance_slug`` values + any format errors."""
    ce = d / "calc_expression.csv"
    if not ce.is_file():
        return set(), []
    slugs: set[str] = set()
    errors: list[str] = []
    for r in _csv_rows(ce):
        s = (r.get("provenance_slug") or "").strip()
        if not s:
            continue
        if _REGRESSION_SLUG_RE.match(s):
            slugs.add(s)
        else:
            errors.append(f"calc_expression provenance_slug {_trunc(s)} is not a valid slug")
    return slugs, errors


def _strip_code_fences(text: str) -> str:
    """Drop fenced code blocks so a ``](./x.md)`` shown as an example inside a fence
    isn't mistaken for a real cross-reference.

    A fence is ``` ``` ``` or ``~~~`` (≥3 of the same char) with <4 leading spaces
    (CommonMark: 4+ spaces is an indented code block, not a fence); only a fence of
    the *same* char closes the block.
    """
    out: list[str] = []
    fence: str | None = None  # the opening marker char while inside a fence
    for line in text.split("\n"):
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        is_fence = indent < 4 and stripped[:3] in ("```", "~~~")
        if is_fence:
            marker = stripped[0]
            if fence is None:
                fence = marker  # open
            elif marker == fence:
                fence = None  # close (matching char)
            continue  # fence delimiters (and mismatched ones inside a block) are never refs
        if fence is None:
            out.append(line)
    return "\n".join(out)


def _regression_follow_refs(
    regdir: Path, stem: str, slugs: set[str], reachable: set[str], queue: list[str]
) -> list[str]:
    """Follow ``stem``'s md cross-references: queue linked companion reports and
    require any directly-referenced ``.svg``/``.json`` sidecar to exist. (Each
    provenance slug's own triple is required separately, independent of link order.)
    """
    md = regdir / f"{stem}.md"
    try:
        text = _strip_code_fences(md.read_text(encoding="utf-8"))
    except OSError as exc:
        return [f"regression/{stem}.md: unreadable ({exc})"]
    errors: list[str] = []
    for name, ext in _REGRESSION_REF_RE.findall(text):
        if ext in ("md", "html"):  # companion report (e.g. lead/lag)
            cmd = f"{name}.md"
            if (regdir / cmd).is_file():
                reachable.add(cmd)
                for sidecar_ext in ("svg", "json"):  # companion sidecars: reachable if present
                    sc = f"{name}.{sidecar_ext}"
                    if (regdir / sc).is_file():
                        reachable.add(sc)
                queue.append(name)
            elif name not in slugs:  # a slug's own missing md is reported by the triple pass
                errors.append(f"regression/{stem}.md links to ./{cmd} which does not exist")
        else:  # directly referenced sidecar — must exist
            fn = f"{name}.{ext}"
            if (regdir / fn).is_file():
                reachable.add(fn)
            else:
                errors.append(f"regression/{stem}.md references ./{fn} which does not exist")
    return errors


def _regression_closure(regdir: Path, slugs: set[str]) -> tuple[set[str], list[str]]:
    """Reports reachable from the provenance slugs → (reachable filenames, errors).

    Every slug requires its full ``{md,svg,json}`` triple (the trio PHP renders),
    enforced in a first pass so the requirement never depends on link-traversal
    order. A second pass follows ``](./X.md)`` / ``](./X.html)`` links to pull in
    companion reports (e.g. lead/lag): a companion requires its linked ``.md`` and
    any sidecar it directly references, while its ``.svg``/``.json`` (if present)
    are marked reachable so they don't warn as orphans.
    """
    reachable: set[str] = set()
    errors: list[str] = []
    # Pass 1: each provenance slug's triple, order-independent.
    for stem in sorted(slugs):
        for ext in ("md", "svg", "json"):
            fn = f"{stem}.{ext}"
            if (regdir / fn).is_file():
                reachable.add(fn)
            else:
                kind = "report" if ext == "md" else "sidecar"
                errors.append(f"regression/{fn} missing ({kind} for provenance_slug {stem!r})")
    # Pass 2: companion reports reachable via md links.
    seen: set[str] = set()
    queue: list[str] = sorted(slugs)
    while queue:
        stem = queue.pop()
        if stem in seen:
            continue
        seen.add(stem)
        if (regdir / f"{stem}.md").is_file():
            errors.extend(_regression_follow_refs(regdir, stem, slugs, reachable, queue))
    return reachable, errors


def _regression_sanitize(regdir: Path, reachable: set[str]) -> list[str]:
    """Run each reachable report file through the security/shape gate; unsafe → error."""
    from kayak.web.regression import (
        UnsafeContentError,
        render_markdown_safe,
        validate_json_sidecar,
        validate_svg,
    )

    errors: list[str] = []
    for name in sorted(reachable):
        path = regdir / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
            if name.endswith(".md"):
                render_markdown_safe(text)
            elif name.endswith(".svg"):
                validate_svg(text)
            elif name.endswith(".json"):
                validate_json_sidecar(text)
        except (UnsafeContentError, OSError) as exc:
            errors.append(f"regression/{name}: {exc}")
    return errors


def _check_regression(d: Path) -> tuple[list[str], list[str]]:
    """provenance_slug ↔ regression report integrity + content sanitization (S2).

    Returns ``(errors, warnings)``. The presence of a ``regression/`` directory is
    the dataset's opt-in: while it is absent the dataset simply publishes no
    regression reports, so declared slugs are a non-fatal WARNING, not an error —
    this keeps the deploy-time validator from bricking a deploy on a dataset that
    declares slugs but hasn't (yet) added the reports. Once the directory exists,
    the check is fully enforced:
    ERROR on a slug with no report/sidecar, a referenced file that is missing, or
    content failing the security/shape gate; WARN on a report referenced by no slug
    (orphan).
    """
    regdir = d / _REGRESSION_DIR
    slugs, errors = _regression_slugs(d)
    if not regdir.is_dir():
        if slugs:  # reports not yet managed by this dataset (transitional, pre file-move)
            return errors, [
                f"{len(slugs)} provenance_slug(s) declared but no regression/ directory "
                "(reports not yet managed by this dataset)"
            ]
        return errors, []  # none configured
    reachable, closure_errors = _regression_closure(regdir, slugs)
    errors.extend(closure_errors)
    errors.extend(_regression_sanitize(regdir, reachable))
    present = {
        p.name for p in regdir.iterdir() if p.is_file() and p.suffix in (".md", ".svg", ".json")
    }
    warnings = [
        f"regression/{f} is not referenced by any provenance_slug report (orphan)"
        for f in sorted(present - reachable)
        if f.lower() != "readme.md"
    ]
    return errors, warnings


def _check_site_yaml(d: Path) -> list[str]:
    """Validate the opt-in ``site.yaml`` (S3a).

    Absent → no error (the dataset keeps the engine's default identity). Present →
    it must parse and every field must pass the typed/safe-shape gate in
    :class:`kayak.dataset.site.SiteConfig` (the values are rendered into HTML by
    both the static build and PHP, so a malformed color, non-http URL, unknown
    key, or HTML-metacharacter in a name is rejected here at the deploy gate).
    """
    if not (d / site.SITE_YAML).is_file():
        return []
    try:
        site.load_site_config(d)
    except ValueError as exc:
        return [str(exc)]
    return []


def _check_state_names(d: Path, good_csvs: set[str]) -> list[str]:
    """Reject state.csv names that aren't safe to use as a URL/filename/HTML text.

    S3b-2 made the served state set data-driven (the build writes ``<name>.html``
    and renders the name), so a path-separator / ``..`` / HTML-metacharacter name
    would path-traverse the staging tree or inject markup. Mirrors the same gate on
    region.yaml keys (:class:`kayak.dataset.region.RegionConfig`).
    """
    if "state" not in good_csvs:
        return []
    errors: list[str] = []
    for row in _csv_rows(d / "state.csv"):
        # Check the RAW cell — the CSV importer (metadata_csv) stores it unstripped,
        # so all_state_names() (and the build) see the raw value; stripping/skipping
        # here would pass "Oregon "/whitespace-only that the build then renders raw.
        name = row.get("name") or ""
        if not layout.is_safe_state_name(name):
            errors.append(f"state.csv: unsafe state name {name!r} (ASCII letter words only)")
    return errors


def _state_names_and_abbreviations(d: Path, good_csvs: set[str]) -> tuple[set[str], set[str]]:
    if "state" not in good_csvs:
        return set(), set()
    state_names: set[str] = set()
    state_abbrevs: set[str] = set()
    for row in _csv_rows(d / "state.csv"):
        name = row.get("name") or ""
        abbreviation = row.get("abbreviation") or ""
        if name:
            state_names.add(name)
        if abbreviation:
            state_abbrevs.add(abbreviation)
    return state_names, state_abbrevs


def _check_state_references(d: Path, good_csvs: set[str]) -> list[str]:
    """State presentation references must point at dataset-owned state.csv rows.

    Static/PHP presentation now derives labels from ``state.csv``. A gauge-state
    abbreviation or ``region.yaml`` state key outside that file would silently lose
    labels or nav resources at build/runtime, so fail it at the dataset gate.
    """
    if "state" not in good_csvs:
        return []
    state_names, state_abbrevs = _state_names_and_abbreviations(d, good_csvs)

    errors: list[str] = []
    errors.extend(_check_gauge_state_references(d, good_csvs, state_abbrevs))
    errors.extend(_check_region_state_references(d, state_names))
    return errors


def _check_gauge_state_references(
    d: Path, good_csvs: set[str], state_abbrevs: set[str]
) -> list[str]:
    if "gauge" in good_csvs:
        return _unknown_gauge_state_errors(d, state_abbrevs)
    return []


def _unknown_gauge_state_errors(d: Path, state_abbrevs: set[str]) -> list[str]:
    unknown_gauge_abbrevs: dict[str, list[str]] = {}
    for row in _csv_rows(d / "gauge.csv"):
        raw = row.get("state") or ""
        for abbreviation in (part.strip() for part in raw.split(",")):
            if abbreviation and abbreviation not in state_abbrevs:
                unknown_gauge_abbrevs.setdefault(abbreviation, []).append(row.get("id") or "?")
    if not unknown_gauge_abbrevs:
        return []
    details = ", ".join(
        f"{abbrev} (gauge ids: {', '.join(ids[:5])}{', ...' if len(ids) > 5 else ''})"
        for abbrev, ids in sorted(unknown_gauge_abbrevs.items())
    )
    return [f"gauge.csv references state abbreviations not in state.csv: {details}"]


def _check_region_state_references(d: Path, state_names: set[str]) -> list[str]:
    if (d / region.REGION_YAML).is_file():
        try:
            region_config = region.load_region_config(d)
        except ValueError:
            # _check_region_yaml reports shape/safety errors; do not duplicate them here.
            return []
        unknown_region_states = sorted(set(region_config.states) - state_names)
        if unknown_region_states:
            return [
                "region.yaml states are not present in state.csv: "
                + ", ".join(repr(s) for s in unknown_region_states)
            ]
    return []


def _check_region_yaml(d: Path) -> list[str]:
    """Validate the opt-in ``region.yaml`` (S3b).

    Absent → no error (the dataset keeps the engine's default region links). Present
    → it must parse and every field must pass the typed/safe-shape gate in
    :class:`kayak.dataset.region.RegionConfig` (the per-state links render into the
    ``{state}.html`` landing pages, so a bad URL, non-http link, unknown key, or
    HTML-metacharacter in a label is rejected here at the deploy gate).
    """
    if not (d / region.REGION_YAML).is_file():
        return []
    try:
        region.load_region_config(d)
    except ValueError as exc:
        return [str(exc)]
    return []


def _check_map_yaml(d: Path) -> list[str]:
    """Validate the opt-in ``map.yaml`` (S3d).

    Absent → no error (the dataset keeps the engine's default map config). Present →
    it must parse and every field must pass the typed/safe-shape gate in
    :class:`kayak.dataset.map.MapConfig` (presentation + popup link render into the
    map JS / an ``href``, and the ArcGIS endpoint/out_fields/bbox drive
    ``fetch-map-layers``, so a bad color, non-http URL, bad shape/popup key, duplicate
    layer key/output filename, unknown key, or unsafe filename is rejected here at
    the deploy gate).
    """
    if not (d / dataset_map.MAP_YAML).is_file():
        return []
    try:
        dataset_map.load_map_config(d)
    except ValueError as exc:
        return [str(exc)]
    return []


# Prose pages (S3c/S3i). Publishable datasets must carry their legal/contact
# pages; about is optional.
_SITE_PROSE_PAGES: tuple[str, ...] = ("about", "disclaimer", "privacy", "contact")
_PUBLISHABLE_REQUIRED_PROSE: tuple[str, ...] = ("privacy", "disclaimer", "contact")


def _is_publishable_dataset(d: Path) -> bool:
    try:
        meta = contract.load_dataset_meta(d)
    except ValueError:
        return False
    return isinstance(meta, dict) and meta.get("status") == "publishable"


def _missing_publishable_prose_errors(site_dir: Path) -> list[str]:
    return [
        f"site/{page}.md is required for a publishable dataset"
        for page in _PUBLISHABLE_REQUIRED_PROSE
        if not (site_dir / f"{page}.md").is_file()
    ]


def _check_one_site_prose_page(
    md: Path, page: str, render_markdown_to_html: Callable[[str], str]
) -> list[str]:
    if md.is_symlink():
        return [f"site/{page}.md: symlinks are not supported"]
    if md.exists() and not md.is_file():
        return [f"site/{page}.md: must be a regular file"]
    if not md.is_file():
        return []

    errors: list[str] = []
    try:
        html = render_markdown_to_html(md.read_text(encoding="utf-8")).lower()
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        errors.append(f"site/{page}.md: failed to render ({exc})")
        return errors
    if "<script" in html or "onerror" in html or "javascript:" in html:
        errors.append(f"site/{page}.md: rendered output contains active content")
    return errors


def _check_site_prose(d: Path) -> list[str]:
    """Validate dataset-owned site prose markdown (S3c/S3i).

    Each present ``site/<page>.md`` must render to clean HTML via the S2 sanitizer.
    For ``publishable`` datasets the legal/contact trio (privacy, disclaimer,
    contact) is required unconditionally, so a production dataset cannot publish
    with only the engine's generic PHP fallbacks. ``scaffold`` datasets may still
    omit ``site/`` while being assembled.
    """
    from kayak.web.regression import render_markdown_to_html

    site_dir = d / "site"
    publishable = _is_publishable_dataset(d)

    if site_dir.is_symlink():
        return ["site: symlinks are not supported"]

    if site_dir.exists() and not site_dir.is_dir():
        path_errors = ["site: must be a directory"]
        if publishable:
            path_errors.extend(_missing_publishable_prose_errors(site_dir))
        return path_errors

    if not site_dir.is_dir():
        if publishable:
            return _missing_publishable_prose_errors(site_dir)
        return []

    errors: list[str] = []
    for page in _SITE_PROSE_PAGES:
        errors.extend(
            _check_one_site_prose_page(site_dir / f"{page}.md", page, render_markdown_to_html)
        )

    if publishable:
        errors.extend(_missing_publishable_prose_errors(site_dir))
    return errors


def _check_site_assets(d: Path) -> list[str]:
    """Validate optional public image overrides under ``site/assets/`` (S3 assets)."""
    return dataset_assets.validate_site_assets(d)


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
            # Anti-vacuity: every reach.csv row must materialize. A loader that
            # silently skipped rows would make the check-reaches scan below pass
            # over fewer reaches than the dataset declares (restores the count
            # guard from test_committed_reach_geom, removed in #124).
            csv_reaches = len(_csv_rows(dataset_dir / "reach.csv"))
            (db_reaches,) = conn.execute("SELECT COUNT(*) FROM reach").fetchone()
            if db_reaches != csv_reaches:
                out.append(
                    f"loader materialized {db_reaches} reaches but reach.csv declares "
                    f"{csv_reaches} (rows skipped on load?)"
                )
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
    warnings: list[str] = []
    errors = validate_dataset(dataset_dir, warnings)
    for w in warnings:
        print(f"  ! {w}")  # advisory — does not affect the exit code
    if errors:
        print(f"dataset validation FAILED ({dataset_dir}):")
        for e in errors:
            print(f"  - {e}")
        return 1
    suffix = f" ({len(warnings)} warning(s))" if warnings else ""
    print(f"dataset validation OK ({dataset_dir}){suffix}")
    return 0
