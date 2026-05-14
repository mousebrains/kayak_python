"""Schema parity test — gate against ORM ↔ migration drift.

The live DB schema is recorded in ``tests/fixtures/live_schema.sql``
(regenerated via ``scripts/regenerate_schema_snapshot.sh``). This test
builds two fresh in-memory DBs:

1. **Snapshot side** — load the checked-in SQL into a tmp SQLite.
2. **ORM side** — ``Base.metadata.create_all()`` on a tmp SQLite.

Both are introspected via ``sqlite_master`` + the standard PRAGMA
queries (``table_info`` / ``index_list`` / ``foreign_key_list``) and
deep-compared as dicts. Any divergence prints the first mismatching
table.

Drift scenarios this catches:

- Migration adds a column, ``models.py`` doesn't → snapshot has the
  column, ORM doesn't → fail.
- Migration drops a column, ``models.py`` still has it → snapshot
  missing, ORM has → fail.
- ``models.py`` changed without a migration → ORM has change, snapshot
  doesn't → fail.
- Migration applied but snapshot not regenerated → snapshot stale, ORM
  has change → fail; forces ``regenerate_schema_snapshot.sh`` to be
  run.

Auto-generated SQLite indexes (``sqlite_autoindex_*``, created for
UNIQUE/PK constraints) are normalised so the comparison stays stable
across SQLite versions and across the snapshot's reload path.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from kayak.db.models import Base

SNAPSHOT = Path(__file__).resolve().parent.parent / "fixtures" / "live_schema.sql"


# SQLite has 5 type affinities (TEXT, NUMERIC, INTEGER, REAL, BLOB) and
# treats declared names as hints. SQLAlchemy emits "FLOAT" while a hand-
# written migration may use "REAL" or "DOUBLE PRECISION" — all three
# bind to REAL affinity, store as IEEE 754, and round-trip identically.
# Rebuilding tables just to align the declared name has no semantic
# effect, so we normalise here rather than treat it as drift. Document
# any addition below with the live-side rationale.
_TYPE_NORMALISATIONS = {
    "FLOAT": "REAL",
    "DOUBLE PRECISION": "REAL",
    "DOUBLE": "REAL",
}


def _normalise_type(typ: str) -> str:
    return _TYPE_NORMALISATIONS.get(typ.upper(), typ)


def _introspect(db_path: Path) -> dict[str, dict[str, object]]:
    """Return ``{table: {columns, indexes, foreign_keys}}`` for every user table.

    Excludes:
      - ``sqlite_*`` system tables
      - ``schema_migrations`` (bookkeeping, not part of the ORM)
      - Auto-generated indexes (``sqlite_autoindex_*``) — these are
        created implicitly for UNIQUE/PK constraints; their name embeds
        a sequence number that isn't stable across schema rebuilds, but
        the underlying constraint is captured in ``columns`` (``pk``
        flag) and the explicit ``UNIQUE`` index list.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        tables = sorted(
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' "
                "AND name != 'schema_migrations'"
            )
        )
        out: dict[str, dict[str, object]] = {}
        for table in tables:
            cols = [
                # (name, type, notnull, dflt, pk) — drop cid (positional) so
                # column reorderings between migration-applied and ORM
                # don't surface as drift when the set is identical. Type
                # name is normalised via _TYPE_NORMALISATIONS.
                (name, _normalise_type(typ), bool(notnull), dflt, int(pk))
                for _cid, name, typ, notnull, dflt, pk in conn.execute(
                    f"PRAGMA table_info({table})"
                )
            ]
            indexes = []
            for _seq, ix_name, ix_unique, _origin, _partial in conn.execute(
                f"PRAGMA index_list({table})"
            ):
                if ix_name.startswith("sqlite_autoindex_"):
                    continue
                cols_in_ix = [
                    name for _seqno, _cid, name in conn.execute(f"PRAGMA index_info({ix_name})")
                ]
                indexes.append((ix_name, bool(ix_unique), cols_in_ix))
            fks = sorted(
                (table_to, from_col, to_col, on_update, on_delete)
                for _id, _seq, table_to, from_col, to_col, on_update, on_delete, _match in conn.execute(
                    f"PRAGMA foreign_key_list({table})"
                )
            )
            out[table] = {
                "columns": sorted(cols),
                "indexes": sorted(indexes),
                "foreign_keys": fks,
            }
        return out
    finally:
        conn.close()


def _build_snapshot_db(tmp_path: Path) -> Path:
    """Load the checked-in snapshot into a fresh SQLite file."""
    db = tmp_path / "snapshot.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(SNAPSHOT.read_text())
    finally:
        conn.close()
    return db


def _build_orm_db(tmp_path: Path) -> Path:
    """Build a fresh SQLite via Base.metadata.create_all()."""
    db = tmp_path / "orm.db"
    eng = create_engine(f"sqlite:///{db}")
    try:
        Base.metadata.create_all(eng)
    finally:
        eng.dispose()
    return db


def _first_diff(actual: dict, expected: dict) -> str:
    """Return a single-table diff string for the first mismatch."""
    only_actual = set(actual) - set(expected)
    only_expected = set(expected) - set(actual)
    if only_actual:
        return f"Tables only in ORM (not in snapshot): {sorted(only_actual)}"
    if only_expected:
        return f"Tables only in snapshot (not in ORM): {sorted(only_expected)}"
    for table in sorted(actual):
        if actual[table] != expected[table]:
            return (
                f"Table '{table}' differs.\n"
                f"  ORM:      {actual[table]}\n"
                f"  Snapshot: {expected[table]}"
            )
    return "(no diff but dicts inequal — check ordering)"


def test_snapshot_fixture_exists() -> None:
    """Fixture must be present; regenerate via scripts/regenerate_schema_snapshot.sh."""
    assert SNAPSHOT.exists(), (
        f"Schema snapshot missing at {SNAPSHOT}. "
        "Run scripts/regenerate_schema_snapshot.sh to generate it."
    )


def test_orm_matches_live_snapshot(tmp_path: Path) -> None:
    """ORM schema must match the live snapshot.

    Catches migration↔model drift. When this fails, the next step is
    usually one of:

    - Add the missing column/index/FK to ``src/kayak/db/models.py``.
    - Drop the now-stale column/index from ``models.py``.
    - Re-run ``scripts/regenerate_schema_snapshot.sh`` after applying a
      migration that updated the live DB.
    """
    if not SNAPSHOT.exists():
        pytest.skip(f"Snapshot {SNAPSHOT} not present; run regen script.")

    snap_db = _build_snapshot_db(tmp_path)
    orm_db = _build_orm_db(tmp_path)

    actual = _introspect(orm_db)
    expected = _introspect(snap_db)

    assert actual == expected, "ORM/snapshot drift:\n" + _first_diff(actual, expected)
