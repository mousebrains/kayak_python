"""``levels generate-sources <dir>`` — generate source.csv + fetch_url.csv from the
authoritative ``sources.yaml`` registry (dataset-separation S1, expand phase).

``sources.yaml`` (in the dataset root) is the human-edited authority for every
source and fetch URL; this command writes the two CSVs the metadata sync consumes,
**deterministically** (LF line endings, rows sorted by id, written atomically; the
column order preserves the committed file's header — see :func:`_column_order`) so
re-running it on an unchanged registry is a no-op. ``--check`` regenerates into a
temp dir and byte-compares the committed CSVs — the CI gate against hand-edits and
drift.

``--from-csv`` reverse-engineers a ``sources.yaml`` from existing CSVs (the one-time
bootstrap, kept as the documented re-bootstrap path).

This is the *expand* phase: it does not change the runtime. ``levels fetch`` /
``init-db`` still read the engine's ``src/kayak/data/sources.yaml`` until the later
S1-fetch slice.
"""

from __future__ import annotations

import argparse
import csv
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from kayak.dataset import layout

SOURCES_YAML = "sources.yaml"

# The two mutually-exclusive "kind" references a source may carry: a fetch_url
# (fetched) or a calc_expression (computed). A source with neither is a detached
# USGS-OGC source. At most one may be set (enforced by validate_registry).
_SOURCE_REF_FIELDS = ("fetch_url_id", "calc_expression_id")


def _load_registry(dataset_dir: Path) -> dict[str, Any]:
    path = dataset_dir / SOURCES_YAML
    if not path.is_file():
        raise ValueError(f"missing {SOURCES_YAML} in {dataset_dir}")
    meta = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(meta, dict):
        raise ValueError(f"{SOURCES_YAML}: top-level value must be a mapping")
    return meta


def _cell(value: object) -> object:
    """Render a YAML value as its CSV cell — ``None``/missing -> empty string."""
    return "" if value is None else value


def _column_order(source_dir: Path, table: str) -> list[str]:
    """Column order for ``<table>.csv``.

    Preserve the committed file's header order when present, else fall back to the
    canonical model order. Column order is **non-semantic** per the dataset
    contract (:mod:`kayak.dataset.layout` validates headers as a *set*; the loaders
    key by name), and the two writers of these CSVs disagree on it: the prod
    nightly snapshot (``scripts/export_metadata.py``) emits the live DB's physical
    ``PRAGMA table_info`` order, which for an ``ALTER``-added column like
    ``source.timezone`` (migration 0008) lands *last* — not at the model position.
    Preserving the committed order keeps ``--check`` a test of row *content* (the
    real completeness invariant) rather than flagging that benign ordering, and
    avoids reordering the committed CSVs (which the snapshot would only revert).

    The column *set* is still validated against the schema, so a drifted header
    (a stray or missing column) is rejected, not silently propagated.
    """
    expected = layout.expected_columns(table)
    path = source_dir / f"{table}.csv"
    if path.is_file():
        with path.open(newline="", encoding="utf-8") as fh:
            header = next(csv.reader(fh), None)
        if header is not None:
            if set(header) != expected:
                raise ValueError(
                    f"{table}.csv header {sorted(header)} does not match the schema "
                    f"columns {sorted(expected)}"
                )
            return header
    return layout.ordered_columns(table)


def _write_csv(out_dir: Path, table: str, rows: list[dict[str, Any]], cols: list[str]) -> None:
    """Write ``<table>.csv`` atomically into ``out_dir``: given column order, LF, by id."""
    ordered = sorted(rows, key=lambda r: int(r["id"]))
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=f".{table}.", suffix=".csv")
    try:
        with open(fd, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(cols)
            for r in ordered:
                w.writerow([_cell(r.get(c)) for c in cols])
        Path(tmp).replace(out_dir / f"{table}.csv")
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _registry_to_rows(meta: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """Map the registry to source.csv / fetch_url.csv row dicts."""
    fetch_rows: list[dict[str, Any]] = []
    for fu in meta.get("fetch_urls") or []:
        fetch_rows.append(
            {
                "id": fu["id"],
                "url": fu["url"],
                "parser": fu["parser"],
                "hours": fu.get("hours"),
                "is_active": 1 if fu.get("enabled", True) else 0,
            }
        )
    source_rows: list[dict[str, Any]] = []
    for s in meta.get("sources") or []:
        source_rows.append(
            {
                "id": s["id"],
                "name": s["name"],
                "agency": s.get("agency"),  # nullable in the schema
                "timezone": s.get("timezone"),
                "fetch_url_id": s.get("fetch_url_id"),
                "calc_expression_id": s.get("calc_expression_id"),
            }
        )
    return source_rows, fetch_rows


def generate(dataset_dir: Path) -> None:
    """Write source.csv + fetch_url.csv from ``dataset_dir/sources.yaml``."""
    meta = _load_registry(dataset_dir)
    problems = validate_registry(meta, dataset_dir)
    if problems:
        raise ValueError("invalid sources.yaml:\n  - " + "\n  - ".join(problems))
    source_rows, fetch_rows = _registry_to_rows(meta)
    _write_csv(dataset_dir, "fetch_url", fetch_rows, _column_order(dataset_dir, "fetch_url"))
    _write_csv(dataset_dir, "source", source_rows, _column_order(dataset_dir, "source"))


def _is_int(value: object) -> bool:
    """A real integer — not ``None``, not a YAML-quoted ``"1"``, not ``bool``."""
    return isinstance(value, int) and not isinstance(value, bool)


def _structural_problems(fetch_urls: list[dict], sources: list[dict]) -> list[str]:
    """Required-field presence + integer-typed ids. Run before every other check:
    the dup/reference/counter checks and ``_registry_to_rows`` all assume these
    hold. id/ref fields must be true ints — a YAML-quoted ``id: "1"`` would alias a
    separate ``id: 1`` (distinct to the set-based dup check) and a string id slips
    past the stale-counter check, so both write a colliding/over-range CSV id with
    no problem reported. Reject them here instead of silently coercing."""
    problems: list[str] = []
    for i, fu in enumerate(fetch_urls):
        for f in ("id", "url", "parser"):
            if fu.get(f) is None:
                problems.append(f"fetch_url[{i}]: missing required field {f!r}")
        if fu.get("id") is not None and not _is_int(fu["id"]):
            problems.append(f"fetch_url[{i}]: id must be an integer, got {fu['id']!r}")
    for i, s in enumerate(sources):
        for f in ("id", "name"):
            if s.get(f) is None:
                problems.append(f"source[{i}]: missing required field {f!r}")
        for f in ("id", *_SOURCE_REF_FIELDS):
            if s.get(f) is not None and not _is_int(s[f]):
                problems.append(f"source[{i}]: {f} must be an integer, got {s[f]!r}")
    return problems


def validate_registry(meta: dict[str, Any], dataset_dir: Path) -> list[str]:
    """Field/reference checks for the registry (empty == valid)."""
    fetch_urls = meta.get("fetch_urls") or []
    sources = meta.get("sources") or []

    # Structural gaps first: the rest (and _registry_to_rows) assume these hold.
    problems = _structural_problems(fetch_urls, sources)
    if problems:
        return problems

    fu_ids = [fu.get("id") for fu in fetch_urls]
    if len(fu_ids) != len(set(fu_ids)):
        problems.append("duplicate fetch_url id(s)")
    src_ids = [s.get("id") for s in sources]
    if len(src_ids) != len(set(src_ids)):
        problems.append("duplicate source id(s)")

    # Parser names must be registered engine parsers.
    from kayak.parsers.registry import ensure_all_loaded, get_parser_names

    ensure_all_loaded()
    known_parsers = set(get_parser_names())
    for fu in fetch_urls:
        if fu.get("parser") not in known_parsers:
            problems.append(f"fetch_url {fu.get('id')}: unknown parser {fu.get('parser')!r}")

    fu_id_set = {fu.get("id") for fu in fetch_urls}
    calc_ids = _calc_expression_ids(dataset_dir)
    for s in sources:
        refs = [f for f in _SOURCE_REF_FIELDS if s.get(f) is not None]
        if len(refs) > 1:
            problems.append(f"source {s.get('id')}: at most one of {_SOURCE_REF_FIELDS}")
        if s.get("fetch_url_id") is not None and s["fetch_url_id"] not in fu_id_set:
            problems.append(f"source {s.get('id')}: fetch_url_id {s['fetch_url_id']} not defined")
        if (
            s.get("calc_expression_id") is not None
            and calc_ids is not None
            and s["calc_expression_id"] not in calc_ids
        ):
            problems.append(
                f"source {s.get('id')}: calc_expression_id {s['calc_expression_id']} "
                "not in calc_expression.csv"
            )

    problems.extend(_check_id_counters(dataset_dir, "source", src_ids))
    problems.extend(_check_id_counters(dataset_dir, "fetch_url", fu_ids))
    return problems


def _calc_expression_ids(dataset_dir: Path) -> set[int] | None:
    """Ids in calc_expression.csv, or None if the file is absent (skip the check)."""
    path = dataset_dir / "calc_expression.csv"
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as fh:
        return {int(r["id"]) for r in csv.DictReader(fh) if (r.get("id") or "").strip()}


def _check_id_counters(dataset_dir: Path, table: str, ids: list[Any]) -> list[str]:
    """next_id (if id_counters.csv present) must exceed every id of *table*."""
    path = dataset_dir / layout.ID_COUNTERS_CSV
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as fh:
        counters = {r["table"]: r["next_id"] for r in csv.DictReader(fh)}
    if table not in counters:
        return []
    nxt = int(counters[table])
    bad = [i for i in ids if isinstance(i, int) and i >= nxt]
    return [f"{table}: id(s) {sorted(bad)} >= next_id {nxt} (stale counter)"] if bad else []


def reverse_engineer(dataset_dir: Path) -> None:
    """Bootstrap ``sources.yaml`` from existing source.csv + fetch_url.csv."""
    fetch_urls: list[dict[str, Any]] = []
    with (dataset_dir / "fetch_url.csv").open(encoding="utf-8") as fh:
        for r in sorted(csv.DictReader(fh), key=lambda r: int(r["id"])):
            entry: dict[str, Any] = {"id": int(r["id"]), "url": r["url"], "parser": r["parser"]}
            if (r.get("hours") or "").strip():
                # hours is a comma-separated UTC-hour list (VARCHAR; e.g. "6,12,18"),
                # not a single int — keep it verbatim so multi-hour specs survive.
                entry["hours"] = r["hours"]
            entry["enabled"] = (r.get("is_active") or "").strip() != "0"
            fetch_urls.append(entry)
    sources: list[dict[str, Any]] = []
    with (dataset_dir / "source.csv").open(encoding="utf-8") as fh:
        for r in sorted(csv.DictReader(fh), key=lambda r: int(r["id"])):
            entry = {"id": int(r["id"]), "name": r["name"], "agency": r["agency"]}
            if (r.get("timezone") or "").strip():
                entry["timezone"] = r["timezone"]
            if (r.get("fetch_url_id") or "").strip():
                entry["fetch_url_id"] = int(r["fetch_url_id"])
            if (r.get("calc_expression_id") or "").strip():
                entry["calc_expression_id"] = int(r["calc_expression_id"])
            sources.append(entry)
    header = (
        "# Authoritative source registry (dataset-separation S1). Edit this; run\n"
        "# `levels generate-sources <dir>` to (re)write source.csv + fetch_url.csv.\n"
    )
    body = yaml.safe_dump(
        {"fetch_urls": fetch_urls, "sources": sources}, sort_keys=False, default_flow_style=False
    )
    (dataset_dir / SOURCES_YAML).write_text(header + body, encoding="utf-8")


def _check(dataset_dir: Path) -> int:
    """Regenerate into a temp dir and byte-compare the committed CSVs."""
    meta = _load_registry(dataset_dir)
    problems = validate_registry(meta, dataset_dir)
    if problems:
        print("invalid sources.yaml:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    absent = [n for n in ("source.csv", "fetch_url.csv") if not (dataset_dir / n).is_file()]
    if absent:
        print(
            f"generate-sources --check: missing {absent}; run `levels generate-sources` first.",
            file=sys.stderr,
        )
        return 1
    source_rows, fetch_rows = _registry_to_rows(meta)
    # Order is taken from the committed files (the byte-comparison target), so a
    # benign PRAGMA-vs-model column-order difference never trips --check.
    fetch_cols = _column_order(dataset_dir, "fetch_url")
    source_cols = _column_order(dataset_dir, "source")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write_csv(tmp, "fetch_url", fetch_rows, fetch_cols)
        _write_csv(tmp, "source", source_rows, source_cols)
        diffs = [
            name
            for name in ("source.csv", "fetch_url.csv")
            if (tmp / name).read_bytes() != (dataset_dir / name).read_bytes()
        ]
    if diffs:
        print(
            f"generate-sources --check: {diffs} differ from sources.yaml. "
            "Run `levels generate-sources` and commit, or fix sources.yaml.",
            file=sys.stderr,
        )
        return 1
    print("generate-sources --check: source.csv + fetch_url.csv match sources.yaml.")
    return 0


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = subparsers.add_parser(
        "generate-sources",
        help="Generate source.csv + fetch_url.csv from a dataset's sources.yaml",
    )
    p.add_argument("dir", help="Dataset directory (containing sources.yaml)")
    p.add_argument(
        "--check",
        action="store_true",
        help="Verify the committed CSVs match sources.yaml (no write); exit 1 on drift",
    )
    p.add_argument(
        "--from-csv",
        action="store_true",
        help="Reverse-engineer sources.yaml from existing source.csv + fetch_url.csv (bootstrap)",
    )
    p.set_defaults(func=_main)


def _main(args: argparse.Namespace) -> int:
    dataset_dir = Path(args.dir)
    if not dataset_dir.is_dir():
        print(f"generate-sources: not a directory: {dataset_dir}", file=sys.stderr)
        return 2
    if args.from_csv:
        reverse_engineer(dataset_dir)
        print(f"wrote {dataset_dir / SOURCES_YAML} from source.csv + fetch_url.csv")
        return 0
    try:
        if args.check:
            return _check(dataset_dir)
        generate(dataset_dir)
    except ValueError as e:
        print(f"generate-sources: {e}", file=sys.stderr)
        return 1
    print(f"generate-sources: wrote source.csv + fetch_url.csv in {dataset_dir}")
    return 0
