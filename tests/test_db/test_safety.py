"""Tests for the dataset-owned-table write guard (kayak.db.safety)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kayak.db import safety
from kayak.db.safety import (
    ProductionWriteRefused,
    maintenance_target_db,
    refuse_configured_db,
    resolve_db_path,
)


def test_resolve_db_path_url_and_bare() -> None:
    assert resolve_db_path("sqlite:////a/b.db") == Path("/a/b.db")
    assert resolve_db_path("/a/b.db") == Path("/a/b.db")


def test_resolve_db_path_rejects_empty_url() -> None:
    with pytest.raises(ValueError, match="no database path"):
        resolve_db_path("sqlite://")


def test_refuse_configured_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    prod = tmp_path / "live.db"
    monkeypatch.setattr(safety, "DATABASE_URL", f"sqlite:///{prod}")

    # The ambient default (target=None) IS the configured DB → refused.
    with pytest.raises(ProductionWriteRefused):
        refuse_configured_db(None)
    # An explicit target equal to the configured DB → refused (path or sqlite URL).
    with pytest.raises(ProductionWriteRefused):
        refuse_configured_db(str(prod))
    with pytest.raises(ProductionWriteRefused):
        refuse_configured_db(f"sqlite:///{prod}")

    # A different (scratch/dev) DB → allowed.
    refuse_configured_db(str(tmp_path / "scratch.db"))

    # The explicit override allows writing the configured DB.
    refuse_configured_db(str(prod), allow_production=True)
    refuse_configured_db(None, allow_production=True)


def test_maintenance_target_db_write_ignores_kayak_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A WRITE target is the explicit --db, else the configured DB — never KAYAK_DB,
    so the legacy env can't become a silent --apply target (review P3)."""
    configured = tmp_path / "configured.db"
    monkeypatch.setattr(safety, "DATABASE_URL", f"sqlite:///{configured}")
    monkeypatch.setenv("KAYAK_DB", str(tmp_path / "kayakdb.db"))

    # No explicit --db → configured (NOT the KAYAK_DB env).
    assert maintenance_target_db(None, for_write=True) == configured
    # Explicit --db wins.
    scratch = tmp_path / "scratch.db"
    assert maintenance_target_db(str(scratch), for_write=True) == scratch


def test_maintenance_target_db_read_falls_back_to_kayak_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A READ may fall back to KAYAK_DB (then the configured DB)."""
    configured = tmp_path / "configured.db"
    kayak_db = tmp_path / "kayakdb.db"
    monkeypatch.setattr(safety, "DATABASE_URL", f"sqlite:///{configured}")
    monkeypatch.setenv("KAYAK_DB", str(kayak_db))
    assert maintenance_target_db(None, for_write=False) == kayak_db
    monkeypatch.delenv("KAYAK_DB")
    assert maintenance_target_db(None, for_write=False) == configured


def test_maintenance_target_db_normalizes_sqlite_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An explicit sqlite:// URL resolves to its on-disk path (so it can be handed to
    sqlite3.connect — review P3: a URL --db used to fail to open)."""
    monkeypatch.setattr(safety, "DATABASE_URL", f"sqlite:///{tmp_path / 'configured.db'}")
    assert maintenance_target_db("sqlite:////a/b.db", for_write=True) == Path("/a/b.db")
    assert maintenance_target_db("/a/b.db", for_write=True) == Path("/a/b.db")
