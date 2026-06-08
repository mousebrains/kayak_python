"""CSV ↔ SQLite metadata machinery for ``levels sync-metadata``.

The **upsert** side (the sole caller is now ``levels sync-metadata``; the geometry
sidecars go through ``scripts/import_metadata.py``, which no longer touches this
module) loads ``data/db/*.csv`` rows by primary key —
``INSERT … ON CONFLICT(<pk>) DO UPDATE`` — so a CSV may omit columns
(``reach.geom`` / ``reach.gradient_profile``, applied via the JSON sidecars) and
have them survive on existing rows. **Exception:** a *generator-owned optional*
column (:func:`kayak.dataset.layout.optional_columns`, e.g.
``fetch_url.unknown_station_policy``) that the CSV omits is RESET to its default
(NULL) — for those columns "absent" means "the default", not "keep the DB value",
so the dataset's "no column ⇒ default" meaning holds at the sync layer too
(otherwise a stale opt-in would linger after an opt-out). This deliberately
differs from the ``EXCLUDED_COLUMNS`` sidecar columns above (``reach.geom`` /
``reach.gradient_profile``), which an omitted CSV must preserve. The **delete**
side (used only by the sync) removes
rows present
in the DB but absent from the CSV. Together they are the "single source of
truth" apply: a reviewed CSV diff lands on prod by stable id (INSERT new /
UPDATE changed / DELETE removed), preserving the observations of surviving
sources — their integer FKs stay valid because the id never moves.

The numeric ``id`` is the stable, author-assigned key and FKs stay numeric, so
this keys on the PK directly — no symbolic-key resolution.

**Foreign-key PRAGMA — the caller owns it, and it matters:** the **sync runs with
``foreign_keys=ON``** so the schema's own ``ON DELETE CASCADE`` / ``SET NULL`` clean a
deleted row's dependents and the ``observation`` ``RESTRICT`` is enforced.
``apply_deletions`` REQUIRES ``foreign_keys=ON`` — cascades do NOT fire under OFF.
SQLite ignores a ``foreign_keys`` change *inside a transaction*, so the caller must set
the PRAGMA before any DML opens one (see ``cli/sync_metadata.py``).

The upsert helpers are PRAGMA-agnostic; only ``apply_deletions`` cares.
"""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

# Parents before children (FK-dependency topological order).
LOAD_ORDER: list[str] = [
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
# Children before parents — the delete pass. A removed *parent* cascades its
# junction/cache rows; a junction row removed on its own is deleted explicitly,
# and goes before its parents here.
DELETE_ORDER: list[str] = list(reversed(LOAD_ORDER))


def _pk_cols(conn: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    """``[(name, declared_type), …]`` for ``table``'s PK columns, in PK order.

    ``PRAGMA table_info`` row = ``(cid, name, type, notnull, dflt, pk)``; ``pk``
    > 0 numbers the column within a (possibly composite) primary key.
    """
    info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    pk = sorted((c for c in info if c[5]), key=lambda c: c[5])
    return [(c[1], c[2]) for c in pk]


# ---------------------------------------------------------------------------
# Upsert side (INSERT new + UPDATE changed) — used by levels sync-metadata.
# ---------------------------------------------------------------------------


def build_upsert_sql(conn: sqlite3.Connection, table: str, header: list[str]) -> str:
    """``INSERT … ON CONFLICT(<pk>) DO UPDATE SET <non-pk cols>`` for ``table``.

    Keying the conflict on the PK lets a CSV omit columns and have them survive
    on existing rows — unlike ``INSERT OR REPLACE``, which delete+reinserts and
    nulls them. A PK-less table falls back to REPLACE.
    """
    pk_cols = [c for c, _ in _pk_cols(conn, table)]
    cols = ", ".join(f'"{c}"' for c in header)
    placeholders = ", ".join("?" for _ in header)
    insert = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
    if not pk_cols:
        return f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({placeholders})"
    conflict = ", ".join(f'"{c}"' for c in pk_cols)
    update_cols = [c for c in header if c not in pk_cols]
    if not update_cols:
        # Every CSV column is part of the PK — a conflict is an identical row.
        return f"{insert} ON CONFLICT({conflict}) DO NOTHING"
    set_clause = ", ".join(f'"{c}" = excluded."{c}"' for c in update_cols)
    return f"{insert} ON CONFLICT({conflict}) DO UPDATE SET {set_clause}"


def import_table(conn: sqlite3.Connection, csv_path: Path) -> int:
    """Upsert every row of ``<table>.csv``; returns the row count."""
    table = csv_path.stem
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return 0
        sql = build_upsert_sql(conn, table, header)
        rows = 0
        batch: list[tuple[str | None, ...]] = []
        for row in reader:
            # Empty string → NULL; sqlite3 would otherwise store "".
            batch.append(tuple(v if v != "" else None for v in row))
            if len(batch) >= 1000:
                conn.executemany(sql, batch)
                rows += len(batch)
                batch = []
        if batch:
            conn.executemany(sql, batch)
            rows += len(batch)
    _reset_absent_optional_columns(conn, table, header)
    return rows


def _reset_absent_optional_columns(conn: sqlite3.Connection, table: str, header: list[str]) -> None:
    """Reset generator-owned OPTIONAL columns to their default (NULL) when the CSV
    omits them, so "absent column ⇒ the default" holds in the DB too.

    The upsert only touches columns named in the CSV header, so without this an
    existing value (e.g. ``fetch_url.unknown_station_policy='ignore'`` from a prior
    opt-in) would survive a CSV that has since dropped the column — leaving the
    runtime ignoring undeclared stations even though the dataset says "default
    reject". Only :func:`layout.optional_columns` are reset; the schema's
    ``EXCLUDED_COLUMNS`` sidecar columns (``reach.geom`` / ``reach.gradient_profile``)
    are intentionally NOT touched — an omitted CSV must preserve them (they're
    applied via the JSON sidecars). The ``IS NOT NULL`` guard keeps a no-op sync a
    no-op (no write when there's nothing to clear).
    """
    from kayak.dataset import layout

    absent = layout.optional_columns(table) - set(header)
    if not absent:
        return
    db_cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for col in sorted(absent & db_cols):
        conn.execute(f'UPDATE {table} SET "{col}" = NULL WHERE "{col}" IS NOT NULL')


def upsert_csvs(conn: sqlite3.Connection, in_dir: Path) -> dict[str, int]:
    """Upsert every present metadata CSV in FK-dependency order.

    Returns ``{table: rows}``. PRAGMA-agnostic — the caller sets foreign_keys.
    """
    counts: dict[str, int] = {}
    for table in LOAD_ORDER:
        csv_path = in_dir / f"{table}.csv"
        if csv_path.exists():
            counts[table] = import_table(conn, csv_path)
    return counts


# ---------------------------------------------------------------------------
# Integrity helpers.
# ---------------------------------------------------------------------------


def integrity_ok(conn: sqlite3.Connection) -> bool:
    """``PRAGMA integrity_check`` is "ok" (no DB corruption)."""
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return bool(row) and row[0] == "ok"


def foreign_key_violations(conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    """The ``PRAGMA foreign_key_check`` rows (empty == clean)."""
    return list(conn.execute("PRAGMA foreign_key_check").fetchall())


# ---------------------------------------------------------------------------
# Delete side (sync only) — diff the CSVs against the DB, then delete absentees.
# ---------------------------------------------------------------------------


def _cast_pk(val: str | None, decl_type: str) -> object:
    """Cast a CSV PK cell to the type the DB stores it as, so CSV and DB PK
    tuples compare equal.

    SQLite returns INTEGER PKs as ``int`` and REAL as ``float``; the CSV reader
    yields strings. Without this, ``"5"`` != ``5`` would read a surviving row as
    BOTH an insert and a delete — the top correctness trap of the sync.
    """
    if val is None or val == "":
        return None
    t = decl_type.upper()
    if "INT" in t:
        return int(val)
    if "REAL" in t or "FLOA" in t or "DOUB" in t:
        return float(val)
    return val


def csv_pks(conn: sqlite3.Connection, in_dir: Path, table: str) -> set[tuple[object, ...]]:
    """PK tuples present in ``<table>.csv`` (typed to match the DB)."""
    csv_path = in_dir / f"{table}.csv"
    pk = _pk_cols(conn, table)
    if not pk or not csv_path.exists():
        return set()
    types = dict(pk)
    names = [c for c, _ in pk]
    out: set[tuple[object, ...]] = set()
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return set()  # header-less/empty file — nothing to diff (like absent)
        missing = [c for c in names if c not in reader.fieldnames]
        if missing:
            # Without every PK column, row.get() yields (None, …) tuples that
            # match nothing → every CSV row reads as an insert and every DB row
            # as a delete. Refuse loudly rather than churn the table.
            raise ValueError(
                f"{table}.csv is missing primary-key column(s) {missing} "
                "— refusing to diff (every row would read as both insert and delete)"
            )
        for row in reader:
            out.add(tuple(_cast_pk(row.get(c), types[c]) for c in names))
    return out


def db_pks(conn: sqlite3.Connection, table: str) -> set[tuple[object, ...]]:
    """PK tuples currently in the DB ``table``."""
    pk = [c for c, _ in _pk_cols(conn, table)]
    if not pk:
        return set()
    cols = ", ".join(f'"{c}"' for c in pk)
    return {tuple(r) for r in conn.execute(f"SELECT {cols} FROM {table}").fetchall()}


def source_observation_counts(conn: sqlite3.Connection, source_ids: set[int]) -> dict[int, int]:
    """``{source_id: observation count}`` — the loud, irreversible number a
    source delete would drop."""
    out: dict[int, int] = {}
    for sid in source_ids:
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM observation WHERE source_id = ?", (sid,)
        ).fetchone()
        out[sid] = int(n)
    return out


@dataclass
class SyncPlan:
    """What a sync would do, computed read-only:

    ``insert_pks`` = rows in the CSV but not the DB (new); ``delete_pks`` = rows
    in the DB but not the CSV (removed); ``source_obs_drops`` = observations a
    source delete would drop. Rows in both are re-upserted (UPDATE) — not listed
    here since they're not the risk surface.
    """

    insert_pks: dict[str, set[tuple[object, ...]]] = field(default_factory=dict)
    delete_pks: dict[str, set[tuple[object, ...]]] = field(default_factory=dict)
    source_obs_drops: dict[int, int] = field(default_factory=dict)

    @property
    def has_deletes(self) -> bool:
        return any(self.delete_pks.values())

    @property
    def total_inserts(self) -> int:
        return sum(len(v) for v in self.insert_pks.values())

    @property
    def total_deletes(self) -> int:
        return sum(len(v) for v in self.delete_pks.values())

    @property
    def total_obs_dropped(self) -> int:
        return sum(self.source_obs_drops.values())


def compute_plan(conn: sqlite3.Connection, in_dir: Path) -> SyncPlan:
    """Read-only diff of every present ``<table>.csv`` against the DB."""
    plan = SyncPlan()
    for table in LOAD_ORDER:
        if not (in_dir / f"{table}.csv").exists():
            continue
        cpks = csv_pks(conn, in_dir, table)
        dpks = db_pks(conn, table)
        inserts = cpks - dpks
        deletes = dpks - cpks
        if inserts:
            plan.insert_pks[table] = inserts
        if deletes:
            plan.delete_pks[table] = deletes
    # source.id is INTEGER, so _cast_pk already typed these as int at runtime.
    removed_source_ids = {cast(int, pk[0]) for pk in plan.delete_pks.get("source", set())}
    if removed_source_ids:
        plan.source_obs_drops = source_observation_counts(conn, removed_source_ids)
    return plan


def apply_deletions(conn: sqlite3.Connection, plan: SyncPlan) -> dict[str, int]:
    """Delete rows absent from the CSVs. **REQUIRES ``foreign_keys=ON``** so the
    schema's ``ON DELETE CASCADE`` / ``SET NULL`` clean dependents and the
    ``observation`` ``RESTRICT`` is enforced.

    A removed source's observations are deleted first (RESTRICT blocks the
    source delete otherwise); then metadata rows go in ``DELETE_ORDER`` and the
    cascades remove junction + ``latest_*`` rows (and NULL
    ``latest_gauge_observation.source_id``). Returns ``{table: rows deleted}``.
    """
    deleted: dict[str, int] = {}
    removed_source_ids = {pk[0] for pk in plan.delete_pks.get("source", set())}
    if removed_source_ids:
        placeholders = ", ".join("?" for _ in removed_source_ids)
        cur = conn.execute(
            f"DELETE FROM observation WHERE source_id IN ({placeholders})",
            tuple(removed_source_ids),
        )
        deleted["observation"] = cur.rowcount
    for table in DELETE_ORDER:
        pks = plan.delete_pks.get(table)
        if not pks:
            continue
        pk_names = [c for c, _ in _pk_cols(conn, table)]
        where = " AND ".join(f'"{c}" = ?' for c in pk_names)
        n = 0
        for pk_tuple in pks:
            cur = conn.execute(f"DELETE FROM {table} WHERE {where}", pk_tuple)
            n += cur.rowcount
        deleted[table] = n
    return deleted
