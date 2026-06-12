"""``levels generate-sources <dir>`` — generate source.csv + fetch_url.csv +
gauge_source.csv from the authoritative ``sources.yaml`` registry (dataset-separation S1).

``sources.yaml`` (in the dataset root) is the human-edited authority for every
source, its fetch URL, and its (single, required) ``gauge_id``; this command writes
the three CSVs the metadata sync consumes — ``source.csv``, ``fetch_url.csv``, and
``gauge_source.csv`` (one ``(gauge_id, source.id)`` row per source) —
**deterministically** (LF line endings, rows sorted by primary key, written
atomically; the column order preserves the committed file's header — see
:func:`_column_order`) so re-running it on an unchanged registry is a no-op.
``--check`` regenerates into a temp dir and byte-compares the committed CSVs — the
CI gate against hand-edits and drift.

``--from-csv`` reverse-engineers a ``sources.yaml`` from existing CSVs (the one-time
bootstrap, kept as the documented re-bootstrap path).

The CSVs this writes are applied to the live DB by ``levels sync-metadata``;
``levels fetch`` then reads the DB, not any YAML (S1-fetch). A URL may carry an
optional ``unknown_station_policy: ignore`` to opt that feed out of the default
reject (S1-fetch-2); the column is written only when at least one URL sets it.
``init-db`` is schema-only (the S1-cleanup removed the former engine-side
``sources.yaml`` seed); a fresh DB gets all metadata via ``levels sync-metadata``.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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


def _column_order(
    source_dir: Path, table: str, rows: list[dict[str, Any]] | None = None
) -> list[str]:
    """Column order for ``<table>.csv``.

    Preserve the committed file's header order when present, else fall back to the
    canonical model order. Column order is **non-semantic** per the dataset
    contract (:mod:`kayak.dataset.layout` validates headers as a *set*; the loaders
    key by name), and the two writers of these CSVs disagree on it: the recovery
    dump (``levels recover-metadata``) emits the live DB's physical
    ``PRAGMA table_info`` order, which for an ``ALTER``-added column like
    ``source.timezone`` (migration 0008) lands *last* — not at the model position.
    Preserving the committed order keeps ``--check`` a test of row *content* (the
    real completeness invariant) rather than flagging that benign ordering, and
    avoids reordering the committed CSVs (which the snapshot would only revert).

    The column *set* is still validated against the schema, so a drifted header
    (a stray, or a missing *required*, column) is rejected, not silently
    propagated. An :func:`layout.optional_columns` column (e.g. ``fetch_url`` 's
    ``unknown_station_policy``) is present **iff a ``rows`` value uses it** —
    independent of whether the committed header carried it. So opt-in/opt-out is
    symmetric: a dataset that opts into nothing stays byte-identical (no column),
    a first opt-in appends the column, and a later opt-out *drops* it again
    (``--check`` flags the stale committed file until regenerated — an opt-out is a
    real content change). The drop is safe because the sync layer resets an absent
    optional column to its default (see ``kayak.db.metadata_csv``), so a removed
    opt-in can't leave a stale value live.
    """
    expected = layout.expected_columns(table)
    optional = layout.optional_columns(table)
    required = expected - optional
    used = {c for c in optional for r in (rows or []) if str(r.get(c) or "").strip()}
    path = source_dir / f"{table}.csv"
    order_source: list[str] | None = None
    if path.is_file():
        with path.open(newline="", encoding="utf-8") as fh:
            header = next(csv.reader(fh), None)
        if header is not None:
            hset = set(header)
            missing = required - hset
            unexpected = hset - expected
            if missing or unexpected:
                raise ValueError(
                    f"{table}.csv header {sorted(header)} does not match the schema "
                    f"columns {sorted(expected)} (missing {sorted(missing)}, "
                    f"unexpected {sorted(unexpected)})"
                )
            order_source = header
    if order_source is None:
        order_source = layout.ordered_columns(table)
    # Keep every required column in the committed (or model) order, plus only the
    # optional columns currently in use; then append a first-time opt-in column
    # that the order source didn't have yet.
    cols = [c for c in order_source if c not in optional or c in used]
    cols += [c for c in layout.ordered_columns(table) if c in used and c not in cols]
    return cols


def _write_csv(out_dir: Path, table: str, rows: list[dict[str, Any]], cols: list[str]) -> None:
    """Write ``<table>.csv`` atomically into ``out_dir``: given column order, LF,
    rows sorted by the table's primary-key tuple (a single ``id``, or the composite
    ``(gauge_id, source_id)`` for gauge_source).

    DUAL-WRITER CONTRACT: this sort must stay byte-identical to the recovery dump
    (``levels recover-metadata``), whose ``order = ", ".join(pk_cols)`` SQL
    ``ORDER BY`` is the same primary-key tuple. The byte round-trip ``--check`` is the
    only guard on this alignment — if recover-metadata's ordering or the layout PK
    spec changes, both writers must move together."""
    pk = layout.primary_key_columns(table)
    ordered = sorted(rows, key=lambda r: tuple(int(r[c]) for c in pk))
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


def _registry_to_rows(meta: dict[str, Any]) -> tuple[list[dict], list[dict], list[dict]]:
    """Map the registry to source.csv / fetch_url.csv / gauge_source.csv row dicts.

    Each source carries a required ``gauge_id``; it is NOT a source.csv column —
    it projects to one ``gauge_source`` row ``(gauge_id, source.id)``, the single
    mandatory gauge every source is bound to (source->gauge is 1-to-1)."""
    fetch_rows: list[dict[str, Any]] = []
    for fu in meta.get("fetch_urls") or []:
        row: dict[str, Any] = {
            "id": fu["id"],
            "url": fu["url"],
            "parser": fu["parser"],
            "hours": fu.get("hours"),
            "is_active": 1 if fu.get("enabled", True) else 0,
        }
        # Emit the optional unknown_station_policy column ONLY when a URL sets it,
        # so a dataset that opts into nothing keeps fetch_url.csv free of the column
        # (byte-identical under --check). _column_order appends it when present.
        policy = fu.get("unknown_station_policy")
        if policy is not None and str(policy).strip():
            row["unknown_station_policy"] = policy
        fetch_rows.append(row)
    source_rows: list[dict[str, Any]] = []
    gauge_source_rows: list[dict[str, Any]] = []
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
        gauge_source_rows.append({"gauge_id": s["gauge_id"], "source_id": s["id"]})
    return source_rows, fetch_rows, gauge_source_rows


def generate(dataset_dir: Path) -> None:
    """Write source.csv + fetch_url.csv + gauge_source.csv from ``dataset_dir/sources.yaml``."""
    meta = _load_registry(dataset_dir)
    problems = validate_registry(meta, dataset_dir)
    if problems:
        raise ValueError("invalid sources.yaml:\n  - " + "\n  - ".join(problems))
    source_rows, fetch_rows, gs_rows = _registry_to_rows(meta)
    _write_csv(
        dataset_dir, "fetch_url", fetch_rows, _column_order(dataset_dir, "fetch_url", fetch_rows)
    )
    _write_csv(
        dataset_dir, "source", source_rows, _column_order(dataset_dir, "source", source_rows)
    )
    _write_csv(
        dataset_dir, "gauge_source", gs_rows, _column_order(dataset_dir, "gauge_source", gs_rows)
    )


def _is_int(value: object) -> bool:
    """A real integer — not ``None``, not a YAML-quoted ``"1"``, not ``bool``."""
    return isinstance(value, int) and not isinstance(value, bool)


def _nonempty_str(value: object) -> bool:
    """A non-blank string — guards required text fields against a list/number
    hand-edit (e.g. ``url: [a, b]``) that would render a junk CSV cell."""
    return isinstance(value, str) and bool(value.strip())


def _timezone_problems(i: int, s: dict) -> list[str]:
    """timezone (optional) must be a valid IANA name: ``BaseParser._localize`` does
    ``ZoneInfo(tz)`` at fetch time, so a bogus value (``"Mars/Phobos"``) crashes
    the parse for that source. Blank/absent = no timezone (naive treated as UTC)."""
    tz = s.get("timezone")
    if tz is None:
        return []
    if not isinstance(tz, str):
        return [f"source[{i}]: timezone must be an IANA name string, got {tz!r}"]
    if not tz.strip():
        return []
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return [f"source[{i}]: timezone {tz!r} is not a valid IANA timezone"]
    return []


def _hours_problems(i: int, fu: dict) -> list[str]:
    """hours is written verbatim to the CSV and parsed at fetch time by
    ``fetch._hour_allowed`` (``int()`` per comma token, compared against the
    current UTC hour 0-23). A non-scalar (e.g. the YAML list ``[6, 12]``), a
    non-numeric token, an out-of-range hour (``"24"`` can never match), or a
    non-empty-but-tokenless spec (``","``) would render a cell that silently
    disables the URL on every constrained fetch — require the documented form: a
    comma-separated list of UTC hours 0-23 (empty string = always)."""
    h = fu.get("hours")
    if h is None:
        return []
    if isinstance(h, bool) or not isinstance(h, (str, int)):
        return [f'fetch_url[{i}]: hours must be a string like "6,12,18", got {h!r}']
    cell = str(h)
    tokens = [t.strip() for t in cell.split(",") if t.strip()]
    tokenless = bool(cell.strip()) and not tokens  # e.g. "," — non-empty, no hours
    ok = all(t.isascii() and t.isdigit() and 0 <= int(t) <= 23 for t in tokens)
    if tokenless or not ok:
        return [
            f"fetch_url[{i}]: hours must be comma-separated UTC hours 0-23 "
            f'(e.g. "6,12,18"), got {h!r}'
        ]
    return []


def _fetch_url_structural(i: int, fu: dict) -> list[str]:
    problems: list[str] = []
    for f in ("id", "url", "parser"):
        if fu.get(f) is None:
            problems.append(f"fetch_url[{i}]: missing required field {f!r}")
    if fu.get("id") is not None and not _is_int(fu["id"]):
        problems.append(f"fetch_url[{i}]: id must be an integer, got {fu['id']!r}")
    if fu.get("url") is not None and not _nonempty_str(fu["url"]):
        problems.append(f"fetch_url[{i}]: url must be a non-empty string, got {fu['url']!r}")
    # Must be a non-empty string before _parser_problems tests membership in a set
    # of names — a YAML container would raise "unhashable type" there otherwise.
    if fu.get("parser") is not None and not _nonempty_str(fu["parser"]):
        problems.append(f"fetch_url[{i}]: parser must be a non-empty string, got {fu['parser']!r}")
    # enabled drives is_active via truthiness, so a quoted "false" would silently
    # enable the URL — require a real bool (fail closed), like ids.
    if fu.get("enabled") is not None and not isinstance(fu["enabled"], bool):
        problems.append(f"fetch_url[{i}]: enabled must be true or false, got {fu['enabled']!r}")
    problems.extend(_hours_problems(i, fu))
    problems.extend(_policy_problems(i, fu))
    return problems


def _policy_problems(i: int, fu: dict) -> list[str]:
    """unknown_station_policy (optional; S1) must be blank/absent or one of
    layout.UNKNOWN_STATION_POLICIES. Blank/absent = the default reject; the runtime
    tolerates case, but keep the registry canonical so a typo ('ingore') is caught
    here rather than silently demoted to reject at fetch time."""
    p = fu.get("unknown_station_policy")
    if p is None:
        return []
    if not isinstance(p, str):
        return [f"fetch_url[{i}]: unknown_station_policy must be a string, got {p!r}"]
    if p.strip() and p.strip() not in layout.UNKNOWN_STATION_POLICIES:
        allowed = ", ".join(repr(v) for v in layout.UNKNOWN_STATION_POLICIES)
        return [
            f"fetch_url[{i}]: unknown_station_policy must be one of {allowed} (or omitted), got {p!r}"
        ]
    return []


def _source_structural(i: int, s: dict) -> list[str]:
    problems: list[str] = []
    for f in ("id", "name", "gauge_id"):
        if s.get(f) is None:
            problems.append(f"source[{i}]: missing required field {f!r}")
    for f in ("id", "gauge_id", *_SOURCE_REF_FIELDS):
        if s.get(f) is not None and not _is_int(s[f]):
            problems.append(f"source[{i}]: {f} must be an integer, got {s[f]!r}")
    if s.get("name") is not None and not _nonempty_str(s["name"]):
        problems.append(f"source[{i}]: name must be a non-empty string, got {s['name']!r}")
    if s.get("agency") is not None and not isinstance(s["agency"], str):
        problems.append(f"source[{i}]: agency must be a string, got {s['agency']!r}")
    problems.extend(_timezone_problems(i, s))
    return problems


def _structural_problems(fetch_urls: list[dict], sources: list[dict]) -> list[str]:
    """Required-field presence + correctly-typed scalars. Run before every other
    check: the dup/reference/counter checks and ``_registry_to_rows`` all assume
    these hold. id/ref fields must be true ints — a YAML-quoted ``id: "1"`` would
    alias a separate ``id: 1`` (distinct to the set-based dup check) and a string
    id slips past the stale-counter check, so both write a colliding/over-range
    CSV id with no problem reported. Reject these here instead of silently
    coercing (or, for ``enabled``, silently mis-reading a quoted bool)."""
    problems: list[str] = []
    for i, fu in enumerate(fetch_urls):
        problems.extend(_fetch_url_structural(i, fu))
    for i, s in enumerate(sources):
        problems.extend(_source_structural(i, s))
    return problems


def _section_items(meta: dict[str, Any], key: str) -> tuple[list[dict], list[str]]:
    """Normalize a top-level registry section to a list of mappings.

    A *missing* section (absent / null) is an empty list — a valid empty
    projection. But the non-``--check`` command **overwrites** the CSVs, so a
    malformed shape must fail loudly rather than silently: a non-list section
    (e.g. the ``sources: {}`` typo, which would coerce to empty and truncate the
    CSV) or a non-mapping item (``sources: [bogus]``, which would crash the
    structural checks with an ``AttributeError``) is reported, not swallowed.
    """
    raw = meta.get(key)
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], [f"{key}: must be a list, got {type(raw).__name__}"]
    problems = [
        f"{key}[{i}]: must be a mapping, got {type(item).__name__}"
        for i, item in enumerate(raw)
        if not isinstance(item, dict)
    ]
    return raw, problems


def _length_problems(table: str, entries: list[dict]) -> list[str]:
    """Text fields must fit their schema ``String(n)`` caps (e.g. source.name 256,
    fetch_url.url 512) — otherwise an over-length value passes here and only fails
    later on DB insert at sync time."""
    caps = {c.name: c.max_length for c in layout.column_specs(table) if c.max_length}
    problems: list[str] = []
    for i, e in enumerate(entries):
        for col, cap in caps.items():
            v = e.get(col)
            if isinstance(v, str) and len(v) > cap:
                problems.append(f"{table}[{i}]: {col} exceeds {cap} chars ({len(v)})")
    return problems


def validate_registry(meta: dict[str, Any], dataset_dir: Path) -> list[str]:
    """Field/reference checks for the registry (empty == valid)."""
    fetch_urls, fu_shape = _section_items(meta, "fetch_urls")
    sources, src_shape = _section_items(meta, "sources")

    # Container shape first: a non-list section or non-mapping item would crash the
    # structural checks (or silently truncate the CSVs), so fail loudly here.
    shape_problems = fu_shape + src_shape
    if shape_problems:
        return shape_problems

    # Structural gaps next: the rest (and _registry_to_rows) assume these hold.
    problems = _structural_problems(fetch_urls, sources)
    if problems:
        return problems

    fu_ids = [fu.get("id") for fu in fetch_urls]
    if len(fu_ids) != len(set(fu_ids)):
        problems.append("duplicate fetch_url id(s)")
    src_ids = [s.get("id") for s in sources]
    if len(src_ids) != len(set(src_ids)):
        problems.append("duplicate source id(s)")

    problems.extend(_parser_problems(fetch_urls))
    problems.extend(_reference_problems(sources, fetch_urls, dataset_dir))
    problems.extend(_length_problems("fetch_url", fetch_urls))
    problems.extend(_length_problems("source", sources))
    problems.extend(_check_id_counters(dataset_dir, "source", src_ids))
    problems.extend(_check_id_counters(dataset_dir, "fetch_url", fu_ids))
    return problems


def _parser_problems(fetch_urls: list[dict]) -> list[str]:
    """Every fetch_url parser must name a registered engine parser."""
    from kayak.parsers.registry import ensure_all_loaded, get_parser_names

    ensure_all_loaded()
    known = set(get_parser_names())
    return [
        f"fetch_url {fu.get('id')}: unknown parser {fu.get('parser')!r}"
        for fu in fetch_urls
        if fu.get("parser") not in known
    ]


def _reference_problems(
    sources: list[dict], fetch_urls: list[dict], dataset_dir: Path
) -> list[str]:
    """A source carries at most one of {fetch_url_id, calc_expression_id}, and each
    reference must resolve (the fetch_url is defined; the calc id is in the CSV)."""
    problems: list[str] = []
    fu_id_set = {fu.get("id") for fu in fetch_urls}
    calc_ids = _calc_expression_ids(dataset_dir)
    gauge_ids = _gauge_ids(dataset_dir)
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
        if (
            gauge_ids is not None
            and s.get("gauge_id") is not None
            and s["gauge_id"] not in gauge_ids
        ):
            problems.append(f"source {s.get('id')}: gauge_id {s['gauge_id']} not in gauge.csv")
    return problems


def _gauge_ids(dataset_dir: Path) -> set[int] | None:
    """Ids in gauge.csv, or None if the file is absent (skip the reference check)."""
    path = dataset_dir / "gauge.csv"
    if not path.is_file():
        return None
    ids: set[int] = set()
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            raw = (r.get("id") or "").strip()
            if not raw:
                continue
            try:
                ids.add(int(raw))
            except ValueError:
                raise ValueError(f"gauge.csv: non-integer id {raw!r}") from None
    return ids


def _calc_expression_ids(dataset_dir: Path) -> set[int] | None:
    """Ids in calc_expression.csv, or None if the file is absent (skip the check)."""
    path = dataset_dir / "calc_expression.csv"
    if not path.is_file():
        return None
    ids: set[int] = set()
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            raw = (r.get("id") or "").strip()
            if not raw:
                continue
            try:
                ids.add(int(raw))
            except ValueError:
                raise ValueError(f"calc_expression.csv: non-integer id {raw!r}") from None
    return ids


def _check_id_counters(dataset_dir: Path, table: str, ids: list[Any]) -> list[str]:
    """next_id (if id_counters.csv present) must exceed every id of *table*."""
    path = dataset_dir / layout.ID_COUNTERS_CSV
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as fh:
        counters = {r["table"]: r["next_id"] for r in csv.DictReader(fh)}
    if table not in counters:
        return []
    raw = counters[table]
    if raw is None or not str(raw).strip():
        return [f"{table}: id_counters.csv has no next_id value"]
    try:
        nxt = int(raw)
    except ValueError:
        return [f"{table}: id_counters.csv next_id is not an integer ({raw!r})"]
    bad = [i for i in ids if isinstance(i, int) and i >= nxt]
    return [f"{table}: id(s) {sorted(bad)} >= next_id {nxt} (stale counter)"] if bad else []


def _atomic_write_text(path: Path, text: str) -> None:
    """Write *text* to *path* atomically (temp in the same dir + replace), LF kept."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _normalize_fetch_url_entry(fu: dict[str, Any]) -> dict[str, Any]:
    """Canonical fetch_url registry entry: id, url, parser, [hours if set], enabled."""
    entry: dict[str, Any] = {"id": fu["id"], "url": fu["url"], "parser": fu["parser"]}
    hours = fu.get("hours")
    if hours is not None and str(hours).strip():
        # hours is a comma-separated UTC-hour list (VARCHAR; e.g. "6,12,18"), kept verbatim.
        entry["hours"] = hours
    policy = fu.get("unknown_station_policy")
    if policy is not None and str(policy).strip():
        entry["unknown_station_policy"] = policy
    entry["enabled"] = bool(fu.get("enabled", True))
    return entry


def _normalize_source_entry(s: dict[str, Any]) -> dict[str, Any]:
    """Canonical source registry entry. ``agency`` is ALWAYS emitted (even empty) to
    match reverse_engineer's historical output; the refs/timezone are omitted when
    absent."""
    entry: dict[str, Any] = {
        "id": s["id"],
        "name": s["name"],
        "agency": s.get("agency") or "",
        "gauge_id": s["gauge_id"],  # required: every source is bound to one gauge
    }
    tz = s.get("timezone")
    if tz is not None and str(tz).strip():
        entry["timezone"] = tz
    for ref in _SOURCE_REF_FIELDS:
        if s.get(ref) is not None:
            entry[ref] = s[ref]
    return entry


def _dump_sources_yaml(fetch_urls: list[dict[str, Any]], sources: list[dict[str, Any]]) -> str:
    """Canonical sources.yaml text: header comment + the normalized lists, sorted by
    id. The single serializer shared by reverse_engineer and add-source, so every
    writer produces byte-identical, round-trippable output."""
    fu = [_normalize_fetch_url_entry(e) for e in sorted(fetch_urls, key=lambda e: int(e["id"]))]
    src = [_normalize_source_entry(e) for e in sorted(sources, key=lambda e: int(e["id"]))]
    header = (
        "# Authoritative source registry (dataset-separation S1). Edit this; run\n"
        "# `levels generate-sources <dir>` to (re)write source.csv, fetch_url.csv,\n"
        "# gauge_source.csv.\n"
    )
    body = yaml.safe_dump(
        {"fetch_urls": fu, "sources": src}, sort_keys=False, default_flow_style=False
    )
    return header + body


def _source_to_gauge(dataset_dir: Path, source_ids: set[int]) -> dict[int, int]:
    """Map source_id -> its single gauge_id from gauge_source.csv, failing closed on
    any gauge_source row the scalar registry ``gauge_id`` field can't represent: a
    partially-blank PK cell, a non-integer id, a row whose source_id isn't a real
    source, a source with no row, or a source with >1 row (an exact duplicate or a
    multi-gauge link). Dangling *gauge* refs and duplicate ids are caught by the
    ``validate_registry`` pass reverse_engineer runs on the result."""
    rows: dict[int, list[int]] = {}
    with (dataset_dir / "gauge_source.csv").open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            sid_raw = (r.get("source_id") or "").strip()
            gid_raw = (r.get("gauge_id") or "").strip()
            if not sid_raw and not gid_raw:
                continue  # a truly blank physical line
            if not sid_raw or not gid_raw:
                # one cell blank — a malformed NOT-NULL PK row; don't silently drop it
                raise ValueError(
                    f"gauge_source.csv: row with an empty PK cell "
                    f"(gauge_id={gid_raw!r}, source_id={sid_raw!r})"
                )
            try:
                sid, gid = int(sid_raw), int(gid_raw)
            except ValueError:
                raise ValueError(
                    f"gauge_source.csv: non-integer id ({sid_raw!r}, {gid_raw!r})"
                ) from None
            rows.setdefault(sid, []).append(gid)
    problems: list[str] = []
    dangling = sorted(set(rows) - source_ids)
    if dangling:
        problems.append(f"gauge_source.csv references source ids not in source.csv: {dangling}")
    for sid in sorted(source_ids):
        gids = rows.get(sid, [])
        if not gids:
            problems.append(f"source {sid}: no gauge_source row (every source needs a gauge)")
        elif len(gids) > 1:
            kind = (
                "linked to multiple gauges" if len(set(gids)) > 1 else "duplicate gauge_source rows"
            )
            problems.append(f"source {sid}: {kind} {sorted(gids)} (expected exactly one)")
    if problems:
        raise ValueError("invalid gauge_source.csv:\n  - " + "\n  - ".join(problems))
    return {sid: rows[sid][0] for sid in source_ids}


_REVERSE_REQUIRED = (
    "source.csv",
    "fetch_url.csv",
    "gauge_source.csv",
    "gauge.csv",
    "calc_expression.csv",
)


def reverse_engineer(dataset_dir: Path) -> None:
    """Bootstrap ``sources.yaml`` from existing source.csv + fetch_url.csv + gauge_source.csv.

    Fails closed on a corrupt input rather than silently canonicalizing it away: the
    contract CSVs it needs must be present (so the gauge_id / calc references are
    actually resolvable, not skipped), the gauge_source row checks live in
    ``_source_to_gauge``, and the assembled registry is run through
    ``validate_registry`` (dup ids, dangling gauge/fetch/calc refs, typing, stale
    counters) BEFORE any file is written.

    The guarantee is bounded to the *registry contract* (validate_registry): the
    output is guaranteed to satisfy ``generate-sources --check``. The FULL dataset
    contract — the USGS source-name rule, reaches, cross-table FK, materialization —
    stays ``validate-dataset``'s job; run it after a bootstrap."""
    missing = [f for f in _REVERSE_REQUIRED if not (dataset_dir / f).is_file()]
    if missing:
        raise ValueError(f"cannot reverse-engineer: missing required file(s) {missing}")
    fetch_urls: list[dict[str, Any]] = []
    with (dataset_dir / "fetch_url.csv").open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            entry: dict[str, Any] = {"id": int(r["id"]), "url": r["url"], "parser": r["parser"]}
            if (r.get("hours") or "").strip():
                entry["hours"] = r["hours"]
            if (r.get("unknown_station_policy") or "").strip():
                entry["unknown_station_policy"] = r["unknown_station_policy"]
            entry["enabled"] = (r.get("is_active") or "").strip() != "0"
            fetch_urls.append(entry)
    with (dataset_dir / "source.csv").open(encoding="utf-8") as fh:
        source_rows = list(csv.DictReader(fh))
    src_gauge = _source_to_gauge(dataset_dir, {int(r["id"]) for r in source_rows})
    sources: list[dict[str, Any]] = []
    for r in source_rows:
        sid = int(r["id"])
        entry = {"id": sid, "name": r["name"], "agency": r["agency"], "gauge_id": src_gauge[sid]}
        if (r.get("timezone") or "").strip():
            entry["timezone"] = r["timezone"]
        if (r.get("fetch_url_id") or "").strip():
            entry["fetch_url_id"] = int(r["fetch_url_id"])
        if (r.get("calc_expression_id") or "").strip():
            entry["calc_expression_id"] = int(r["calc_expression_id"])
        sources.append(entry)
    problems = validate_registry({"fetch_urls": fetch_urls, "sources": sources}, dataset_dir)
    if problems:
        raise ValueError(
            "cannot reverse-engineer an invalid dataset:\n  - " + "\n  - ".join(problems)
        )
    _atomic_write_text(dataset_dir / SOURCES_YAML, _dump_sources_yaml(fetch_urls, sources))


def _check(dataset_dir: Path) -> int:
    """Regenerate into a temp dir and byte-compare the committed CSVs."""
    meta = _load_registry(dataset_dir)
    problems = validate_registry(meta, dataset_dir)
    if problems:
        print("invalid sources.yaml:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    targets = ("source.csv", "fetch_url.csv", "gauge_source.csv")
    absent = [n for n in targets if not (dataset_dir / n).is_file()]
    if absent:
        print(
            f"generate-sources --check: missing {absent}; run `levels generate-sources` first.",
            file=sys.stderr,
        )
        return 1
    source_rows, fetch_rows, gs_rows = _registry_to_rows(meta)
    # Order is taken from the committed files (the byte-comparison target), so a
    # benign PRAGMA-vs-model column-order difference never trips --check.
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # Pass the regenerated rows so a newly-used optional column (e.g. a fresh
        # unknown_station_policy opt-in) appends and --check flags the stale CSV.
        _write_csv(
            tmp, "fetch_url", fetch_rows, _column_order(dataset_dir, "fetch_url", fetch_rows)
        )
        _write_csv(tmp, "source", source_rows, _column_order(dataset_dir, "source", source_rows))
        _write_csv(
            tmp, "gauge_source", gs_rows, _column_order(dataset_dir, "gauge_source", gs_rows)
        )
        diffs = [
            name
            for name in targets
            if (tmp / name).read_bytes() != (dataset_dir / name).read_bytes()
        ]
    if diffs:
        print(
            f"generate-sources --check: {diffs} differ from sources.yaml. "
            "Run `levels generate-sources` and commit, or fix sources.yaml.",
            file=sys.stderr,
        )
        return 1
    print(
        "generate-sources --check: source.csv + fetch_url.csv + gauge_source.csv match sources.yaml."
    )
    return 0


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = subparsers.add_parser(
        "generate-sources",
        help="Generate source.csv + fetch_url.csv + gauge_source.csv from a dataset's sources.yaml",
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
        help="Reverse-engineer sources.yaml from the committed CSVs (bootstrap)",
    )
    p.set_defaults(func=_main)


def _main(args: argparse.Namespace) -> int:
    dataset_dir = Path(args.dir)
    if not dataset_dir.is_dir():
        print(f"generate-sources: not a directory: {dataset_dir}", file=sys.stderr)
        return 2
    try:
        if args.from_csv:
            reverse_engineer(dataset_dir)
            print(f"wrote {dataset_dir / SOURCES_YAML} from source/fetch_url/gauge_source CSVs")
            return 0
        if args.check:
            return _check(dataset_dir)
        generate(dataset_dir)
    except ValueError as e:
        print(f"generate-sources: {e}", file=sys.stderr)
        return 1
    print(f"generate-sources: wrote source.csv + fetch_url.csv + gauge_source.csv in {dataset_dir}")
    return 0


# --- add-source: append a source to sources.yaml + allocate its id --------------


def _read_counters(dataset_dir: Path) -> list[list[str]]:
    """id_counters.csv as a list of rows (header first), preserving order — so a
    bump rewrites only the changed value cell, never reorders the file."""
    path = dataset_dir / layout.ID_COUNTERS_CSV
    if not path.is_file():
        raise ValueError(f"missing {layout.ID_COUNTERS_CSV} in {dataset_dir}")
    with path.open(newline="", encoding="utf-8") as fh:
        return [row for row in csv.reader(fh) if row]


def _counter_value(rows: list[list[str]], table: str) -> int:
    """The next_id for *table* (raises ``ValueError`` — caught by the CLI — if the
    dataset has no well-formed counter row for it; do not invent one, a fabricated
    counter could violate the retired-id high-water invariant ``validate-dataset``
    enforces)."""
    for r in rows[1:]:
        if r and r[0] == table:
            if len(r) < 2 or not r[1].strip():
                raise ValueError(f"id_counters.csv: malformed row for table {table!r}: {r}")
            return int(r[1])  # ValueError on a non-numeric cell is caught at the CLI
    raise ValueError(f"id_counters.csv has no row for table {table!r}")


def _set_counter(rows: list[list[str]], table: str, value: int) -> None:
    for r in rows[1:]:
        if len(r) >= 2 and r[0] == table:
            r[1] = str(value)
            return
    raise ValueError(f"id_counters.csv has no row for table {table!r}")  # pragma: no cover


def _write_counters(dataset_dir: Path, rows: list[list[str]]) -> None:
    path = dataset_dir / layout.ID_COUNTERS_CSV
    fd, tmp = tempfile.mkstemp(dir=dataset_dir, prefix=".id_counters.", suffix=".csv")
    try:
        with open(fd, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh, lineterminator="\n").writerows(rows)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _validate_proposed(
    dataset_dir: Path, proposed: dict[str, list[dict[str, Any]]], bumped: list[list[str]]
) -> None:
    """Prove the proposed registry + bumped counters are valid WITHOUT touching the
    real dataset: mirror the validator's inputs into a temp dir (so the stale-counter
    check sees the bumped next_id, not the pre-bump one the new id equals), run a
    generate() dry-run, and apply the source-row dataset-contract rule. Raises
    ValueError on any problem.

    Scope: generate() covers the registry contract (parser/refs/typing); we also run
    ``validate_dataset._check_source_names`` (the one source.csv-CONTENT contract
    rule generate's validate_registry doesn't cover — a USGS source name must be a
    numeric station id). The full dataset contract (cross-table FK, reaches,
    materialization) stays ``levels validate-dataset``'s job — it's run after the
    gauge/gauge_source wiring this command doesn't touch."""
    from kayak.cli.validate_dataset import _check_source_names

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for fn in (
            "calc_expression.csv",
            "gauge.csv",
            "source.csv",
            "fetch_url.csv",
            "gauge_source.csv",
        ):
            src = dataset_dir / fn
            if src.is_file():
                shutil.copy2(src, tmp / fn)
        _write_counters(tmp, bumped)
        _atomic_write_text(tmp / SOURCES_YAML, _dump_sources_yaml(*_split(proposed)))
        generate(tmp)  # validates the registry and writes source.csv/fetch_url.csv
        contract = _check_source_names(tmp, {"source"})
        if contract:
            raise ValueError("dataset-contract violation:\n  - " + "\n  - ".join(contract))


def _split(meta: dict[str, list[dict[str, Any]]]) -> tuple[list[dict], list[dict]]:
    return meta.get("fetch_urls") or [], meta.get("sources") or []


def _add_source_guards(
    dataset_dir: Path,
    sources: list[dict],
    fetch_urls: list[dict],
    *,
    name: str,
    gauge_id: int,
    url: str | None,
    parser: str | None,
    calc_expression_id: int | None,
) -> None:
    """Up-front guards beyond ``validate_registry`` (clear messages; reject before
    any write). Enforced in the library, not just the CLI wrapper, so any caller
    fails closed."""
    if url is not None and calc_expression_id is not None:
        raise ValueError(
            "a source is fetch-backed (--url) or calc-backed (--calc-expression-id), not both"
        )
    if url is not None and parser is None:
        raise ValueError("a fetch_url (url) requires a parser")
    if name != name.strip():
        raise ValueError("name must not have leading/trailing whitespace")
    if any(str(s.get("name")) == name for s in sources):
        raise ValueError(f"a source named {name!r} already exists")
    _check_gauge_ref_exists(dataset_dir, gauge_id)
    if url is not None:
        if url != url.strip():
            raise ValueError("url must not have leading/trailing whitespace")
        if any(str(fu.get("url")) == url for fu in fetch_urls):
            raise ValueError(f"a fetch_url with url {url!r} already exists")
    if calc_expression_id is not None:
        _check_calc_ref_exists(dataset_dir, calc_expression_id)


def _check_gauge_ref_exists(dataset_dir: Path, gauge_id: int) -> None:
    gauge_ids = _gauge_ids(dataset_dir)
    if gauge_ids is None:
        raise ValueError("gauge.csv not present; cannot resolve --gauge-id")
    if gauge_id not in gauge_ids:
        raise ValueError(f"gauge_id {gauge_id} not in gauge.csv")


def _check_calc_ref_exists(dataset_dir: Path, calc_expression_id: int) -> None:
    calc_ids = _calc_expression_ids(dataset_dir)
    if calc_ids is None:
        raise ValueError("calc_expression.csv not present; cannot link --calc-expression-id")
    if calc_expression_id not in calc_ids:
        raise ValueError(f"calc_expression_id {calc_expression_id} not in calc_expression.csv")


def _allocate_id(counters: list[list[str]], table: str) -> int:
    """next_id for *table* with a floor check — ``validate_registry`` only flags ids
    *>=* next_id (a stale ceiling); a corrupt non-positive counter would otherwise
    allocate an invalid id and fail open here."""
    value = _counter_value(counters, table)
    if value < 1:
        raise ValueError(f"id_counters.csv next_id for {table} must be >= 1 (got {value})")
    return value


def add_source(
    dataset_dir: Path,
    *,
    name: str,
    gauge_id: int,
    agency: str | None = None,
    timezone: str | None = None,
    url: str | None = None,
    parser: str | None = None,
    hours: str | None = None,
    enabled: bool = True,
    calc_expression_id: int | None = None,
) -> dict[str, int]:
    """Append a new source (and, with *url*, a new fetch_url) to ``sources.yaml``,
    allocate its stable id(s) from id_counters.csv, bump the counter(s), and
    regenerate the CSVs. Returns the allocated ids ({"source": …[, "fetch_url": …]}).

    Raises ``ValueError`` on any guard/validation failure BEFORE the real dataset is
    touched (the proposed result is validated in a temp dir first)."""
    meta = _load_registry(dataset_dir)
    # The CURRENT registry must be valid before we derive lists from it: _split uses
    # `meta.get(...) or []`, which would treat a malformed-but-falsy section (the
    # `sources: {}` typo) as empty and silently drop the existing rows on rewrite,
    # or crash on a truthy-malformed shape. validate_registry runs the _section_items
    # shape checks, so refuse a broken input before any write.
    current = validate_registry(meta, dataset_dir)
    if current:
        raise ValueError(
            "current sources.yaml is invalid; fix it (e.g. `generate-sources`) before "
            "add-source:\n  - " + "\n  - ".join(current)
        )
    base_fu, base_src = _split(meta)
    fetch_urls, sources = list(base_fu), list(base_src)
    _add_source_guards(
        dataset_dir,
        sources,
        fetch_urls,
        name=name,
        gauge_id=gauge_id,
        url=url,
        parser=parser,
        calc_expression_id=calc_expression_id,
    )

    counters = _read_counters(dataset_dir)
    source_id = _allocate_id(counters, "source")
    allocated: dict[str, int] = {"source": source_id}
    new_source: dict[str, Any] = {"id": source_id, "name": name, "gauge_id": gauge_id}
    if agency is not None:
        new_source["agency"] = agency
    if timezone is not None:
        new_source["timezone"] = timezone

    if url is not None:
        fetch_url_id = _allocate_id(counters, "fetch_url")
        allocated["fetch_url"] = fetch_url_id
        fu_entry: dict[str, Any] = {
            "id": fetch_url_id,
            "url": url,
            "parser": parser,
            "enabled": enabled,
        }
        if hours is not None:
            fu_entry["hours"] = hours
        fetch_urls.append(fu_entry)
        new_source["fetch_url_id"] = fetch_url_id
    elif calc_expression_id is not None:
        new_source["calc_expression_id"] = calc_expression_id
    sources.append(new_source)

    # Bump in memory, then validate against the bumped counters (temp), then commit.
    _set_counter(counters, "source", source_id + 1)
    if "fetch_url" in allocated:
        _set_counter(counters, "fetch_url", allocated["fetch_url"] + 1)
    _validate_proposed(dataset_dir, {"fetch_urls": fetch_urls, "sources": sources}, counters)

    # Commit (authority first, then bookkeeping, then derived artifacts). A crash
    # between the first two writes leaves only a self-reported stale counter, never
    # a silently burned id.
    _atomic_write_text(dataset_dir / SOURCES_YAML, _dump_sources_yaml(fetch_urls, sources))
    _write_counters(dataset_dir, counters)
    generate(dataset_dir)
    return allocated


def add_source_args(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = subparsers.add_parser(
        "add-source",
        help="Add a source to a dataset's sources.yaml (allocates the id, regenerates CSVs)",
        description=(
            "Add a source to a dataset's sources.yaml: allocate its stable id, append the "
            "entry (bound to an existing gauge via --gauge-id), bump id_counters, and "
            "regenerate source.csv + fetch_url.csv + gauge_source.csv. The result is "
            "validated against the registry contract and the source-row rules (incl. the "
            "USGS numeric-station-id name rule). The full dataset contract (reaches, "
            "materialization) is not re-checked here — run `levels validate-dataset` for it."
        ),
    )
    p.add_argument("dir", help="Dataset directory (containing sources.yaml)")
    p.add_argument("--name", required=True, help="Source name (the fetch resolution key)")
    p.add_argument(
        "--gauge-id",
        type=int,
        dest="gauge_id",
        required=True,
        help="Id of the existing gauge this source feeds (every source needs a gauge)",
    )
    p.add_argument("--agency", help="Agency label, e.g. USGS / NWS")
    p.add_argument(
        "--timezone", help="IANA timezone for naive local-time feeds (e.g. America/Boise)"
    )
    p.add_argument("--url", help="Fetch URL — creates a new fetch_url (requires --parser)")
    p.add_argument("--parser", help="Parser name for --url (must be a registered parser)")
    p.add_argument(
        "--hours", help='Comma-separated UTC hours to fetch, e.g. "6,12,18" (with --url)'
    )
    p.add_argument(
        "--disabled", action="store_true", help="Mark the new fetch_url inactive (with --url)"
    )
    p.add_argument(
        "--calc-expression-id",
        type=int,
        dest="calc_expression_id",
        help="Link an existing calc_expression id (calc-backed source)",
    )
    p.set_defaults(func=_add_source_main)


def _add_source_main(args: argparse.Namespace) -> int:
    dataset_dir = Path(args.dir)
    if not dataset_dir.is_dir():
        print(f"add-source: not a directory: {dataset_dir}", file=sys.stderr)
        return 2
    # Manual cross-flag validation (argparse can't express the --url/--parser bundle
    # vs --calc-expression-id mutual exclusion).
    if (args.url is None) != (args.parser is None):
        print("add-source: --url and --parser must be given together", file=sys.stderr)
        return 2
    if args.url is not None and args.calc_expression_id is not None:
        print(
            "add-source: --url/--parser (fetch) and --calc-expression-id (calc) are "
            "mutually exclusive",
            file=sys.stderr,
        )
        return 2
    if args.url is None and (args.hours is not None or args.disabled):
        print("add-source: --hours/--disabled require --url", file=sys.stderr)
        return 2
    try:
        allocated = add_source(
            dataset_dir,
            name=args.name,
            gauge_id=args.gauge_id,
            agency=args.agency,
            timezone=args.timezone,
            url=args.url,
            parser=args.parser,
            hours=args.hours,
            enabled=not args.disabled,
            calc_expression_id=args.calc_expression_id,
        )
    except ValueError as e:
        print(f"add-source: {e}", file=sys.stderr)
        return 1
    ids = ", ".join(f"{t}={i}" for t, i in allocated.items())
    print(f"add-source: added source {args.name!r} ({ids})")
    return 0
