"""Tests for the levels migrate CLI."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text

from kayak.cli import migrate as migrate_mod


def _make_args(**overrides: object) -> Namespace:
    defaults = {"status": False, "check": False, "stamp": [], "stamp_all": False}
    defaults.update(overrides)
    return Namespace(**defaults)


def test_discover_migrations_orders_by_version(tmp_path: Path) -> None:
    (tmp_path / "0002_b.sql").write_text("SELECT 2;")
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    (tmp_path / "README.md").write_text("# not a migration")
    migrate_mod.regenerate_manifest(tmp_path)

    found = migrate_mod.discover_migrations(tmp_path)

    assert [m.version for m in found] == ["0001", "0002"]
    assert [m.name for m in found] == ["0001_a", "0002_b"]
    assert all(len(m.digest) == 64 for m in found)  # sha256 hex from the manifest


def test_regenerate_manifest_rejects_duplicate_version_prefix(tmp_path: Path) -> None:
    # Two files share the 0099 prefix; the prefix is schema_migrations.version
    # (a PK), so a collision must fail loudly when the manifest is built, not on
    # apply (#49/#50).
    (tmp_path / "0099_first.sql").write_text("SELECT 1;")
    (tmp_path / "0099_second.sql").write_text("SELECT 2;")
    with pytest.raises(ValueError, match="duplicate migration version '0099'"):
        migrate_mod.regenerate_manifest(tmp_path)


def test_committed_manifest_matches_files() -> None:
    # The committed manifest.csv must match the real migration files exactly —
    # every file listed with the correct sha256, no orphans, no missing. This is
    # the drift guard: editing/adding a migration without rerunning
    # gen_migration_manifest.py fails here (and discover_migrations would raise).
    import csv
    import hashlib

    manifest = migrate_mod.MIGRATIONS_DIR / migrate_mod.MANIFEST_NAME
    listed: dict[str, str] = {}
    with manifest.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            listed[row["filename"]] = row["sha256"]
    on_disk = {p.name for p in migrate_mod.MIGRATIONS_DIR.glob("*.sql")}
    assert set(listed) == on_disk, "manifest.csv and *.sql files disagree — run gen script"
    for name, sha in listed.items():
        actual = hashlib.sha256((migrate_mod.MIGRATIONS_DIR / name).read_bytes()).hexdigest()
        assert actual == sha, f"sha256 drift for {name} — run gen_migration_manifest.py"
    # And discover (which verifies all of the above) returns unique versions.
    versions = [m.version for m in migrate_mod.discover_migrations()]
    assert versions and len(versions) == len(set(versions))


def test_discover_requires_manifest(tmp_path: Path) -> None:
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")  # no manifest written
    with pytest.raises(ValueError, match="manifest not found"):
        migrate_mod.discover_migrations(tmp_path)


def test_discover_rejects_edited_migration(tmp_path: Path) -> None:
    # Tamper detection: a migration edited after the manifest was generated must
    # fail discovery (its sha256 no longer matches).
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    migrate_mod.regenerate_manifest(tmp_path)
    (tmp_path / "0001_a.sql").write_text("SELECT 2;  -- edited after manifest")
    with pytest.raises(ValueError, match="sha256 mismatch"):
        migrate_mod.discover_migrations(tmp_path)


def test_discover_rejects_unmanifested_sql(tmp_path: Path) -> None:
    # A .sql added without regenerating the manifest is an orphan → hard error.
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    migrate_mod.regenerate_manifest(tmp_path)
    (tmp_path / "0002_b.sql").write_text("SELECT 2;")  # not in the manifest
    with pytest.raises(ValueError, match="not in the manifest"):
        migrate_mod.discover_migrations(tmp_path)


def test_split_statements_rejects_semicolon_in_string_literal() -> None:
    with pytest.raises(ValueError, match="string literal"):
        migrate_mod._split_statements("INSERT INTO t (v) VALUES ('a; b');")


def test_split_statements_rejects_dashdash_in_string_literal() -> None:
    with pytest.raises(ValueError, match="string literal"):
        migrate_mod._split_statements("INSERT INTO t (v) VALUES ('a -- b');")


def test_split_statements_allows_escaped_quote_then_real_semicolon() -> None:
    # '' is an in-literal escaped quote, so the ; after the closing quote is a
    # real top-level separator: two statements, no false rejection.
    assert migrate_mod._split_statements("INSERT INTO t (v) VALUES ('it''s ok'); SELECT 1;") == [
        "INSERT INTO t (v) VALUES ('it''s ok')",
        "SELECT 1",
    ]


def test_split_statements_accepts_every_committed_migration() -> None:
    # Regression: the R5.5 guard must not reject any real migration.
    for path in sorted(migrate_mod.MIGRATIONS_DIR.glob("*.sql")):
        assert migrate_mod._split_statements(path.read_text()), path.name


def test_apply_pending_runs_only_unapplied(
    tmp_path: Path, monkeypatch: object, engine: object
) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_create_widget.sql").write_text(
        "CREATE TABLE widget (id INTEGER PRIMARY KEY, name TEXT);"
    )
    (migrations_dir / "0002_seed_widget.sql").write_text(
        "INSERT INTO widget (name) VALUES ('hello');"
    )
    migrate_mod.regenerate_manifest(migrations_dir)

    with (
        patch("kayak.cli.migrate.MIGRATIONS_DIR", migrations_dir),
        patch("kayak.cli.migrate.get_engine", return_value=engine),
    ):
        ran = migrate_mod.apply_pending()
        assert ran == ["0001", "0002"]
        # Running again is a no-op.
        assert migrate_mod.apply_pending() == []

    with engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM widget")).scalar()
        assert count == 1
        versions = {r[0] for r in conn.execute(text("SELECT version FROM schema_migrations")).all()}
        assert versions == {"0001", "0002"}


def test_check_exits_nonzero_when_a_migration_is_pending(tmp_path: Path, engine: object) -> None:
    # The snapshot/deploy guard: a migration file on disk but not yet in
    # schema_migrations must make `levels migrate --check` exit non-zero, so
    # scripts/snapshot_metadata.sh refuses to snapshot a half-migrated DB.
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_widget.sql").write_text("CREATE TABLE widget (id INTEGER PRIMARY KEY);")
    migrate_mod.regenerate_manifest(migrations_dir)
    with (
        patch("kayak.cli.migrate.MIGRATIONS_DIR", migrations_dir),
        patch("kayak.cli.migrate.get_engine", return_value=engine),
        pytest.raises(SystemExit) as exc,
    ):
        migrate_mod.migrate(_make_args(check=True))
    assert exc.value.code not in (0, None)
    # The message is the operator's signal — it must name the pending version.
    assert "0001" in str(exc.value)


def test_check_passes_when_all_applied(tmp_path: Path, engine: object) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_widget.sql").write_text("CREATE TABLE widget (id INTEGER PRIMARY KEY);")
    migrate_mod.regenerate_manifest(migrations_dir)
    with (
        patch("kayak.cli.migrate.MIGRATIONS_DIR", migrations_dir),
        patch("kayak.cli.migrate.get_engine", return_value=engine),
    ):
        migrate_mod.apply_pending()  # apply 0001
        # Nothing pending now → --check is a clean no-op (must not raise).
        migrate_mod.migrate(_make_args(check=True))


def test_check_passes_when_migrations_dir_absent(tmp_path: Path, engine: object) -> None:
    # No migrations dir at all → discover_migrations() returns [] → --check is a
    # clean pass, never a false abort that would wedge the nightly snapshot.
    missing_dir = tmp_path / "does-not-exist"
    with (
        patch("kayak.cli.migrate.MIGRATIONS_DIR", missing_dir),
        patch("kayak.cli.migrate.get_engine", return_value=engine),
    ):
        migrate_mod.migrate(_make_args(check=True))  # must not raise


def test_stamp_records_without_running(tmp_path: Path, engine: object) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    # SQL that would error if executed (table doesn't exist) — confirms stamp
    # short-circuits the run path.
    (migrations_dir / "0007_broken.sql").write_text("INSERT INTO nonexistent VALUES (1);")
    migrate_mod.regenerate_manifest(migrations_dir)

    with (
        patch("kayak.cli.migrate.MIGRATIONS_DIR", migrations_dir),
        patch("kayak.cli.migrate.get_engine", return_value=engine),
    ):
        migrate_mod.migrate(_make_args(stamp=["0007"]))
        assert migrate_mod.applied_versions() == {"0007"}
        # apply_pending must now skip the broken file.
        assert migrate_mod.apply_pending() == []


def test_no_transaction_marker_disables_fk_cascade(tmp_path: Path, engine: object) -> None:
    """A migration tagged ``@no_transaction`` runs in autocommit so its
    ``PRAGMA foreign_keys=OFF`` actually takes effect. Without the marker the
    PRAGMA is silently ignored mid-transaction and ``DROP TABLE parent`` fires
    ON DELETE CASCADE on every child row. The marker is the fix for the
    incident that wiped reach_state/reach_class/reach_guidebook on 2026-05-03.
    """
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_setup.sql").write_text(
        "CREATE TABLE parent (id INTEGER PRIMARY KEY);\n"
        "CREATE TABLE child (pid INTEGER, "
        "FOREIGN KEY(pid) REFERENCES parent(id) ON DELETE CASCADE);\n"
        "INSERT INTO parent VALUES (1),(2),(3);\n"
        "INSERT INTO child  VALUES (1),(2),(3);\n"
    )
    (migrations_dir / "0002_rebuild_parent.sql").write_text(
        "-- @no_transaction\n"
        "PRAGMA foreign_keys = OFF;\n"
        "BEGIN;\n"
        "CREATE TABLE parent_new (id INTEGER PRIMARY KEY, note TEXT);\n"
        "INSERT INTO parent_new (id) SELECT id FROM parent;\n"
        "DROP TABLE parent;\n"
        "ALTER TABLE parent_new RENAME TO parent;\n"
        "COMMIT;\n"
        "PRAGMA foreign_keys = ON;\n"
    )
    migrate_mod.regenerate_manifest(migrations_dir)

    with (
        patch("kayak.cli.migrate.MIGRATIONS_DIR", migrations_dir),
        patch("kayak.cli.migrate.get_engine", return_value=engine),
    ):
        ran = migrate_mod.apply_pending()
        assert ran == ["0001", "0002"]

    with engine.begin() as conn:
        # Child rows must survive — the cascade would have wiped them if the
        # PRAGMA was ignored.
        assert conn.execute(text("SELECT COUNT(*) FROM child")).scalar() == 3
        # Parent rebuild produced the new column.
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(parent)")).all()}
        assert "note" in cols


def test_0003_does_not_recreate_reach_level(engine: object) -> None:
    """After the 0003 edit, replaying real migration 0003 must not recreate
    the dropped `reach_level` table.

    The reach_level half of the original 0003 was stripped — reach_level is
    gone from models.py and a later commit DROP-ped it. This test runs 0003
    against a schema that matches Base.metadata.create_all() while stamping
    every OTHER migration as already applied, so only 0003 runs. We stamp
    the non-subject migrations because several of them (ADD COLUMN, CREATE
    TABLE without IF NOT EXISTS, etc.) aren't idempotent against a
    create_all-built schema — that's fine in production because init_db
    stamps them on fresh DBs, but the test needs to mirror that.
    """
    from kayak.db.models import Base

    Base.metadata.create_all(engine)
    with (
        patch("kayak.cli.migrate.get_engine", return_value=engine),
    ):
        for m in migrate_mod.discover_migrations():
            if m.version != "0003":
                migrate_mod.stamp(m.version)
        ran = migrate_mod.apply_pending()
        assert ran == ["0003"]

    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='reach_level'")
        ).all()
        assert rows == [], "reach_level must not exist after migration 0003 runs"


def test_ensure_tracking_table_adds_digest_to_legacy_db(engine: object) -> None:
    # The live-host transition: a pre-S9a DB has a 2-column schema_migrations.
    # _ensure_tracking_table must add the `digest` column idempotently and leave
    # legacy rows in place (NULL digest, tolerated).
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE schema_migrations "
                "(version TEXT PRIMARY KEY, applied_at DATETIME NOT NULL)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO schema_migrations (version, applied_at) VALUES ('0001', '2026-01-01')"
            )
        )
    with patch("kayak.cli.migrate.get_engine", return_value=engine):
        migrate_mod._ensure_tracking_table()
        migrate_mod._ensure_tracking_table()  # idempotent — must not raise
    with engine.begin() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(schema_migrations)")).all()}
        assert "digest" in cols
        legacy = conn.execute(
            text("SELECT digest FROM schema_migrations WHERE version='0001'")
        ).scalar()
        assert legacy is None  # legacy row tolerated with a NULL digest


def test_stamp_all_known_records_digests(tmp_path: Path, engine: object) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_a.sql").write_text("SELECT 1;")
    migrate_mod.regenerate_manifest(migrations_dir)
    with (
        patch("kayak.cli.migrate.MIGRATIONS_DIR", migrations_dir),
        patch("kayak.cli.migrate.get_engine", return_value=engine),
    ):
        assert migrate_mod.stamp_all_known() == 1
    with engine.begin() as conn:
        digest = conn.execute(
            text("SELECT digest FROM schema_migrations WHERE version='0001'")
        ).scalar()
    assert digest and len(digest) == 64  # the manifest sha256 was recorded for the new row
