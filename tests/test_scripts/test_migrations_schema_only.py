"""Guard: migrations are schema-only; metadata changes go via CSV + sync.

Since the metadata-single-source redesign, a metadata change — adding, editing,
renaming, or removing a ``source`` / ``gauge`` / ``reach`` / junction row — is a
reviewed ``data/db/*.csv`` diff applied to prod by ``levels sync-metadata``
(matched by the stable id, so a rename is an UPDATE and observations survive). It
is **not** a SQL data migration. ``levels migrate`` now carries **schema** changes
only (CREATE / ALTER / DROP / index / CHECK).

This guard enforces that **every active migration** is schema-only: none may
``INSERT`` / ``UPDATE`` / ``DELETE`` a metadata table. Since S9b relocated the
wire-via-migration era's data migrations out of the active set (the 54 pure-data
ones to ``kayak_data/history/sql/``, the 3 mixed schema+data ones to the engine's
frozen ``legacy/migrations_frozen/``), the active directory is now data-free, so
the grandfather window (``RETIRE_AFTER``) is gone — the rule applies to the whole
set, old and new.

``observation`` and the ``latest_*`` caches are **not** metadata tables (they hold
re-harvestable time-series, not CSV-backed metadata), so DML against them in a
migration is still allowed — e.g. the observation-repair migration 0025.

A schema migration that needs a one-time *data* transform coupled to a DDL change
is the rare case this forbids on purpose: do the DDL in the migration and the data
change as a follow-up CSV edit + sync, or revisit this guard deliberately.
"""

from __future__ import annotations

import re
from pathlib import Path

from kayak.config import DATA_DIR
from kayak.db.metadata_csv import LOAD_ORDER

MIGRATIONS_DIR = DATA_DIR / "db" / "migrations"
# Frozen mixed (schema+data) migrations relocated out of the active set by S9b —
# kept for the non-vacuity check (they still contain real metadata DML).
FROZEN_DIR = Path(__file__).resolve().parents[2] / "legacy" / "migrations_frozen"

# The tables whose rows now live in data/db/*.csv (single source of truth).
# Imported from the loader so the two can't drift.
METADATA_TABLES = set(LOAD_ORDER)

# INSERT [OR ...] INTO <t> / UPDATE [OR ...] <t> / DELETE FROM <t> — capture <t>.
_DML = re.compile(
    r"\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|UPDATE(?:\s+OR\s+\w+)?|DELETE\s+FROM)"
    r'\s+["\'`]?(\w+)',
    re.IGNORECASE,
)


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", "", sql)  # line comments
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)  # block comments
    return sql


def _metadata_dml(sql: str) -> set[str]:
    """Metadata tables ``sql`` writes (INSERT/UPDATE/DELETE), comments stripped."""
    body = _strip_sql_comments(sql)
    return {t for t in (m.lower() for m in _DML.findall(body)) if t in METADATA_TABLES}


def test_active_migrations_are_schema_only() -> None:
    # Every active migration (the whole set since S9b — no grandfather window) must
    # be schema-only: metadata-table rows change via a data/db/*.csv diff + `levels
    # sync-metadata`, never a data migration.
    offenders: dict[str, set[str]] = {}
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        hits = _metadata_dml(path.read_text(encoding="utf-8"))
        if hits:
            offenders[path.name] = hits
    assert not offenders, (
        "active migration(s) write metadata-table rows (INSERT/UPDATE/DELETE) — keep "
        f"migrations schema-only; relocate data to kayak_data: {offenders}"
    )


def test_detector_filters_comments_and_non_metadata_tables() -> None:
    # A commented-out INSERT is not DML.
    assert _metadata_dml("-- INSERT INTO source (x) VALUES (1)\nCREATE TABLE foo(x);") == set()
    # observation / caches are not metadata tables.
    assert _metadata_dml("DELETE FROM observation WHERE source_id = 1;") == set()
    # DDL on a metadata table is fine (that IS a schema migration).
    assert _metadata_dml("ALTER TABLE source ADD COLUMN note TEXT;") == set()
    # The three DML forms against metadata tables are caught.
    assert _metadata_dml("INSERT OR IGNORE INTO gauge (name) VALUES ('g');") == {"gauge"}
    assert _metadata_dml("UPDATE reach SET huc = '170900' WHERE id = 1;") == {"reach"}
    assert _metadata_dml("INSERT INTO source (name) SELECT 'x';") == {"source"}


def test_guard_is_non_vacuous_against_a_frozen_data_migration() -> None:
    # The detector must actually flag real historical metadata DML, so a broken
    # regex can't make the guard silently pass on an empty set. The wire-via-
    # migration era's data files moved out of the active set (S9b), so check a
    # frozen mixed migration that still carries metadata DML.
    frozen_0003 = FROZEN_DIR / "0003_reach_level_class_checks.sql"
    assert frozen_0003.is_file(), f"expected frozen mixed migration at {frozen_0003}"
    assert "reach_class" in _metadata_dml(frozen_0003.read_text())
