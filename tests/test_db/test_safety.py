"""Tests for the dataset-owned-table write guard (kayak.db.safety)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kayak.db import safety
from kayak.db.safety import ProductionWriteRefused, refuse_configured_db, resolve_db_path


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
