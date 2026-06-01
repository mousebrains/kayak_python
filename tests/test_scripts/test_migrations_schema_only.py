"""Guard: migrations are schema-only; metadata changes go via CSV + sync.

Since the metadata-single-source redesign, a metadata change — adding, editing,
renaming, or removing a ``source`` / ``gauge`` / ``reach`` / junction row — is a
reviewed ``data/db/*.csv`` diff applied to prod by ``levels sync-metadata``
(matched by the stable id, so a rename is an UPDATE and observations survive). It
is **not** a SQL data migration. ``levels migrate`` now carries **schema** changes
only (CREATE / ALTER / DROP / index / CHECK).

This guard enforces that going forward: a migration numbered above
``RETIRE_AFTER`` may not ``INSERT`` / ``UPDATE`` / ``DELETE`` any metadata table.
The historical data migrations (≤ 0074, the wire-via-migration era) are
grandfathered — applied migrations are immutable history, and the reconciliation
guard that used to police them is retired with the flow it policed.

``observation`` and the ``latest_*`` caches are **not** metadata tables (they hold
re-harvestable time-series, not CSV-backed metadata), so DML against them in a
migration is still allowed — e.g. an observation-repair migration.

A schema migration that needs a one-time *data* transform coupled to a DDL change
is the rare case this forbids on purpose: do the DDL in the migration and the data
change as a follow-up CSV edit + sync, or revisit this guard deliberately.
"""

from __future__ import annotations

import re

from kayak.config import DATA_DIR
from kayak.db.metadata_csv import LOAD_ORDER

MIGRATIONS_DIR = DATA_DIR / "db" / "migrations"

# Migrations at or below this number predate the retirement (the
# wire-via-migration era); immutable history, grandfathered.
RETIRE_AFTER = 74

# The tables whose rows now live in data/db/*.csv (single source of truth).
# Imported from the loader so the two can't drift.
METADATA_TABLES = set(LOAD_ORDER)

_LEADING_NUM = re.compile(r"^(\d+)")

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


def _migration_number(name: str) -> int:
    m = _LEADING_NUM.match(name)
    return int(m.group(1)) if m else -1


def _metadata_dml(sql: str) -> set[str]:
    """Metadata tables ``sql`` writes (INSERT/UPDATE/DELETE), comments stripped."""
    body = _strip_sql_comments(sql)
    return {t for t in (m.lower() for m in _DML.findall(body)) if t in METADATA_TABLES}


def test_new_migrations_are_schema_only() -> None:
    offenders: dict[str, set[str]] = {}
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if _migration_number(path.name) <= RETIRE_AFTER:
            continue
        hits = _metadata_dml(path.read_text(encoding="utf-8"))
        if hits:
            offenders[path.name] = hits
    assert not offenders, (
        "migration(s) write metadata-table rows (INSERT/UPDATE/DELETE) — metadata "
        "changes now go via a data/db/*.csv diff + `levels sync-metadata`, not a "
        f"data migration. Keep new migrations schema-only: {offenders}"
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


def test_guard_is_non_vacuous_against_the_historical_data_migrations() -> None:
    # The detector must actually flag the wire-via-migration era, so a broken
    # regex can't make the guard silently pass on an empty set.
    wired = _metadata_dml((MIGRATIONS_DIR / "0066_wire_columbia_usgs_gauges.sql").read_text())
    assert {"source", "gauge", "gauge_source"} <= wired, wired
    # And it must exclude observation from a drop migration (proves the
    # metadata-only filter, not a blanket DML match).
    dropped = _metadata_dml((MIGRATIONS_DIR / "0071_drop_bridgeport_gauge.sql").read_text())
    assert dropped == {"gauge", "source"}, dropped
