"""Tests for the levels migrate CLI."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import text

from kayak.cli import migrate as migrate_mod


def _make_args(**overrides: object) -> Namespace:
    defaults = {"status": False, "stamp": [], "stamp_all": False}
    defaults.update(overrides)
    return Namespace(**defaults)


def test_discover_migrations_sorts_by_filename(tmp_path: Path) -> None:
    (tmp_path / "0002_b.sql").write_text("SELECT 2;")
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    (tmp_path / "README.md").write_text("# not a migration")

    found = migrate_mod.discover_migrations(tmp_path)

    assert [m.version for m in found] == ["0001", "0002"]
    assert [m.name for m in found] == ["0001_a", "0002_b"]


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


def test_stamp_records_without_running(tmp_path: Path, engine: object) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    # SQL that would error if executed (table doesn't exist) — confirms stamp
    # short-circuits the run path.
    (migrations_dir / "0007_broken.sql").write_text("INSERT INTO nonexistent VALUES (1);")

    with (
        patch("kayak.cli.migrate.MIGRATIONS_DIR", migrations_dir),
        patch("kayak.cli.migrate.get_engine", return_value=engine),
    ):
        migrate_mod.migrate(_make_args(stamp=["0007"]))
        assert migrate_mod.applied_versions() == {"0007"}
        # apply_pending must now skip the broken file.
        assert migrate_mod.apply_pending() == []


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
