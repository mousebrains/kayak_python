"""Tests for `levels import-metadata` — the packaged sidecar apply (4B)."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from kayak.cli import import_metadata as im


def _args(**kw) -> argparse.Namespace:
    base = {"geom_only": False, "gradient_only": False, "allow_missing_reaches": False}
    base.update(kw)
    return argparse.Namespace(**base)


def _setup(tmp_path: Path, monkeypatch, reach_ids=(1, 2)) -> Path:
    db = tmp_path / "kayak.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE reach (id INTEGER PRIMARY KEY, geom TEXT, gradient_profile TEXT)")
    conn.executemany("INSERT INTO reach (id) VALUES (?)", [(i,) for i in reach_ids])
    conn.commit()
    conn.close()
    monkeypatch.setattr("kayak.config.DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setattr("kayak.config.DATASET_DIR", tmp_path)
    return db


def test_applies_both_sidecars_by_default(tmp_path: Path, monkeypatch) -> None:
    db = _setup(tmp_path, monkeypatch)
    (tmp_path / "reaches.json").write_text('{"1": "g1", "2": "g2"}')
    (tmp_path / "reaches-gradient.json").write_text('{"1": "p1"}')

    assert im.import_metadata(_args()) == 0

    conn = sqlite3.connect(db)
    rows = dict(conn.execute("SELECT id, geom FROM reach").fetchall())
    assert rows == {1: "g1", 2: "g2"}
    (gp,) = conn.execute("SELECT gradient_profile FROM reach WHERE id = 1").fetchone()
    assert gp == "p1"


def test_geom_only_skips_gradient(tmp_path: Path, monkeypatch) -> None:
    db = _setup(tmp_path, monkeypatch)
    (tmp_path / "reaches.json").write_text('{"1": "g1"}')
    (tmp_path / "reaches-gradient.json").write_text('{"1": "p1"}')

    assert im.import_metadata(_args(geom_only=True)) == 0

    conn = sqlite3.connect(db)
    (gp,) = conn.execute("SELECT gradient_profile FROM reach WHERE id = 1").fetchone()
    assert gp is None


def test_unmatched_id_rolls_back_and_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    """A sidecar id with no reach row (ran before sync-metadata / wrong DB)
    must roll back the WHOLE apply and exit non-zero."""
    db = _setup(tmp_path, monkeypatch, reach_ids=(1,))
    (tmp_path / "reaches.json").write_text('{"1": "g1", "99": "orphan"}')

    assert im.import_metadata(_args()) == 1
    assert "matched no reach row" in capsys.readouterr().err
    conn = sqlite3.connect(db)
    (geom,) = conn.execute("SELECT geom FROM reach WHERE id = 1").fetchone()
    assert geom is None, "partial apply must be rolled back"


def test_allow_missing_reaches_permits_partial(tmp_path: Path, monkeypatch) -> None:
    db = _setup(tmp_path, monkeypatch, reach_ids=(1,))
    (tmp_path / "reaches.json").write_text('{"1": "g1", "99": "orphan"}')

    assert im.import_metadata(_args(allow_missing_reaches=True)) == 0
    conn = sqlite3.connect(db)
    (geom,) = conn.execute("SELECT geom FROM reach WHERE id = 1").fetchone()
    assert geom == "g1"


def test_malformed_sidecar_fails_cleanly(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup(tmp_path, monkeypatch)
    (tmp_path / "reaches.json").write_text("not json")
    assert im.import_metadata(_args()) == 1
    assert "malformed" in capsys.readouterr().err
