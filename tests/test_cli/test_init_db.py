"""Tests for kayak.cli.init_db (schema-only initialization + migration stamping).

The seed/sync helpers (_seed_states, sync_sources) were removed by the
dataset-separation S1-cleanup: init-db creates the schema and stamps
migrations only; states and all other metadata load via `levels
sync-metadata` from the dataset.
"""

from argparse import Namespace
from unittest.mock import patch

from sqlalchemy import text


def test_no_seed_flag_is_accepted_noop(engine, capsys):
    """--no-seed survives as a deprecated no-op so existing scripts/docs don't
    break; it must not change behavior and should say so."""
    from kayak.cli.init_db import init_db

    with (
        patch("kayak.cli.init_db.get_engine", return_value=engine),
        patch("kayak.cli.migrate.get_engine", return_value=engine),
    ):
        init_db(Namespace(drop=False, no_seed=True))

    out = capsys.readouterr().out
    assert "deprecated" in out
    with engine.connect() as conn:
        states = conn.execute(text("SELECT COUNT(*) FROM state")).scalar()
        fetch_urls = conn.execute(text("SELECT COUNT(*) FROM fetch_url")).scalar()
    assert states == 0, "init-db must not seed state rows"
    assert fetch_urls == 0, "init-db must not seed fetch_url rows"


def test_init_db_is_schema_only(engine):
    """Plain init-db (no flags) creates empty tables — no seeded metadata."""
    from kayak.cli.init_db import init_db

    with (
        patch("kayak.cli.init_db.get_engine", return_value=engine),
        patch("kayak.cli.migrate.get_engine", return_value=engine),
    ):
        init_db(Namespace(drop=False, no_seed=False))

    with engine.connect() as conn:
        for table in ("state", "source", "fetch_url"):
            count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            assert count == 0, f"init-db must leave {table} empty"


def test_init_db_skips_stamping_on_existing_db(engine, capsys):
    """init-db on a DB that already tracks any migration must not
    blanket-stamp the rest. The prior behavior would silently mark
    unapplied migrations as applied, making `levels migrate` skip them.
    """
    from kayak.cli.init_db import init_db
    from kayak.cli.migrate import _ensure_tracking_table, stamp

    with patch("kayak.cli.migrate.get_engine", return_value=engine):
        _ensure_tracking_table()
        stamp("0001")

    args = Namespace(drop=False, no_seed=True)
    with (
        patch("kayak.cli.init_db.get_engine", return_value=engine),
        patch("kayak.cli.migrate.get_engine", return_value=engine),
    ):
        init_db(args)

    with engine.connect() as conn:
        versions = {r[0] for r in conn.execute(text("SELECT version FROM schema_migrations")).all()}
    assert versions == {"0001"}, "init-db must not stamp anything beyond the existing 0001"
    assert "already tracks" in capsys.readouterr().out


def test_init_db_drop_resets_schema_migrations(engine):
    """`init-db --drop` must clear the raw-DDL schema_migrations too, not just the
    Base tables. Otherwise stale tracking rows survive drop_all, init-db treats
    the freshly create_all'd schema as "already tracked" and skips stamping, and
    `levels migrate` then re-runs migrations the new schema already has (R5.4).
    """
    from kayak.cli import migrate as migrate_mod
    from kayak.cli.init_db import init_db

    with (
        patch("kayak.cli.init_db.get_engine", return_value=engine),
        patch("kayak.cli.migrate.get_engine", return_value=engine),
    ):
        # A stale/behind tracking table surviving from a prior DB.
        migrate_mod.stamp("9999")  # bogus version, not a committed migration
        assert "9999" in migrate_mod.applied_versions()

        init_db(Namespace(drop=True, no_seed=True))

        applied = migrate_mod.applied_versions()
        assert "9999" not in applied, "--drop must clear the stale tracking table"
        # Fresh stamp == exactly the committed migration set, so migrate is a no-op.
        assert applied == {m.version for m in migrate_mod.discover_migrations()}
        assert migrate_mod.apply_pending() == []
