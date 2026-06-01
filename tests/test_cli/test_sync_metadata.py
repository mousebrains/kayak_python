"""Tests for ``levels sync-metadata`` (the incremental prod-apply sync).

The load-bearing guarantee is **observation preservation**: applying a CSV diff
that adds/renames/deletes metadata must keep every surviving source's
observations (matched by the stable id), and a delete must drop ONLY the removed
source's observations + its cascaded dependents. The sync runs on a raw
``sqlite3`` connection with ``foreign_keys=ON``, so these exercise the real
cascade / SET-NULL / RESTRICT behaviour against the actual schema.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine

from kayak.cli.sync_metadata import sync_metadata
from kayak.db.models import Base


def _schema(db: Path) -> None:
    eng = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(eng)
    eng.dispose()


def _exec(db: Path, statements: list[str]) -> None:
    """Seed via a raw connection (consistent parent-first order, so FK state
    doesn't matter for the insert)."""
    conn = sqlite3.connect(db)
    try:
        for sql in statements:
            conn.execute(sql)
        conn.commit()
    finally:
        conn.close()


def _rows(db: Path, sql: str) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(db)
    try:
        return list(conn.execute(sql).fetchall())
    finally:
        conn.close()


def _scalar(db: Path, sql: str) -> object:
    return _rows(db, sql)[0][0]


def _write_csv(csv_dir: Path, table: str, header: list[str], rows: list[list[object]]) -> None:
    with (csv_dir / f"{table}.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def _args(
    db: Path, csv_dir: Path, *, allow_deletes: bool = False, dry_run: bool = False
) -> argparse.Namespace:
    return argparse.Namespace(
        database_url=f"sqlite:///{db}",
        csv_dir=str(csv_dir),
        allow_deletes=allow_deletes,
        dry_run=dry_run,
    )


def _seed_two_sources(db: Path) -> None:
    """fetch_url FU1; sources S1 (3 obs) + S2 (2 obs), both linked to gauge G1;
    a latest_observation + a latest_gauge_observation referencing S2."""
    _schema(db)
    _exec(
        db,
        [
            "INSERT INTO fetch_url (id, url) VALUES (1, 'http://fu1')",
            "INSERT INTO source (id, name, agency, fetch_url_id) VALUES (1, 'S1', 'USGS', 1)",
            "INSERT INTO source (id, name, agency) VALUES (2, 'S2', 'USGS')",
            "INSERT INTO gauge (id, name) VALUES (1, 'G1')",
            "INSERT INTO gauge_source (gauge_id, source_id) VALUES (1, 1)",
            "INSERT INTO gauge_source (gauge_id, source_id) VALUES (1, 2)",
            "INSERT INTO observation VALUES (1, '2026-01-01 00:00', 'flow', 10)",
            "INSERT INTO observation VALUES (1, '2026-01-01 01:00', 'flow', 11)",
            "INSERT INTO observation VALUES (1, '2026-01-01 02:00', 'flow', 12)",
            "INSERT INTO observation VALUES (2, '2026-01-01 00:00', 'flow', 20)",
            "INSERT INTO observation VALUES (2, '2026-01-01 01:00', 'flow', 21)",
            "INSERT INTO latest_observation (source_id, data_type, observed_at, value) "
            "VALUES (2, 'flow', '2026-01-01 01:00', 21)",
            "INSERT INTO latest_gauge_observation (gauge_id, data_type, observed_at, value, source_id) "
            "VALUES (1, 'flow', '2026-01-01 01:00', 21, 2)",
        ],
    )


def _write_surviving_csvs(csv_dir: Path, *, source_name: str = "S1") -> None:
    """CSV desired state: keep S1 (optionally renamed), keep gauge G1, drop S2
    and the (G1,S2) link. fetch_url FU1 stays."""
    _write_csv(csv_dir, "fetch_url", ["id", "url"], [[1, "http://fu1"]])
    _write_csv(
        csv_dir,
        "source",
        ["id", "name", "agency", "fetch_url_id", "calc_expression_id", "timezone"],
        [[1, source_name, "USGS", 1, "", ""]],
    )
    _write_csv(csv_dir, "gauge", ["id", "name"], [[1, "G1"]])
    _write_csv(csv_dir, "gauge_source", ["gauge_id", "source_id"], [[1, 1]])


# ---------------------------------------------------------------------------
# A — observation preservation across add + rename + delete (THE test).
# ---------------------------------------------------------------------------


def test_preserves_observations_on_add_rename_delete(tmp_path: Path) -> None:
    db = tmp_path / "k.db"
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    _seed_two_sources(db)

    # Desired state: rename S1, add gauge G2, delete S2.
    _write_csv(
        csv_dir,
        "source",
        ["id", "name", "agency", "fetch_url_id", "calc_expression_id", "timezone"],
        [[1, "S1_NEW", "USGS", 1, "", ""]],
    )
    _write_csv(csv_dir, "fetch_url", ["id", "url"], [[1, "http://fu1"]])
    _write_csv(csv_dir, "gauge", ["id", "name"], [[1, "G1"], [2, "G2"]])
    _write_csv(csv_dir, "gauge_source", ["gauge_id", "source_id"], [[1, 1]])

    rc = sync_metadata(_args(db, csv_dir, allow_deletes=True))
    assert rc == 0

    # S1 renamed (matched by id — NOT delete+reinsert); S2 gone.
    assert _rows(db, "SELECT id, name FROM source ORDER BY id") == [(1, "S1_NEW")]
    # The crux: S1 keeps ALL three observations; S2's two are gone.
    assert _scalar(db, "SELECT COUNT(*) FROM observation WHERE source_id = 1") == 3
    assert _scalar(db, "SELECT COUNT(*) FROM observation WHERE source_id = 2") == 0
    # New gauge inserted; existing kept.
    assert {r[0] for r in _rows(db, "SELECT id FROM gauge")} == {1, 2}
    # (G1,S2) link cascade-removed; (G1,S1) kept.
    assert _rows(db, "SELECT gauge_id, source_id FROM gauge_source") == [(1, 1)]
    # latest_observation for S2 cascade-removed; latest_gauge_observation.source_id SET NULL.
    assert _scalar(db, "SELECT COUNT(*) FROM latest_observation") == 0
    assert _rows(db, "SELECT source_id FROM latest_gauge_observation") == [(None,)]
    # Clean end state.
    assert _rows(db, "PRAGMA foreign_key_check") == []


# ---------------------------------------------------------------------------
# B — idempotency: a second identical sync is a no-op.
# ---------------------------------------------------------------------------


def test_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "k.db"
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    _seed_two_sources(db)
    _write_surviving_csvs(csv_dir)

    assert sync_metadata(_args(db, csv_dir, allow_deletes=True)) == 0
    after_first = _rows(db, "SELECT id, name FROM source ORDER BY id")
    obs_first = _scalar(db, "SELECT COUNT(*) FROM observation")
    # Second run: nothing left to insert/delete.
    assert sync_metadata(_args(db, csv_dir, allow_deletes=True)) == 0
    assert _rows(db, "SELECT id, name FROM source ORDER BY id") == after_first
    assert _scalar(db, "SELECT COUNT(*) FROM observation") == obs_first


# ---------------------------------------------------------------------------
# C — delete gating + dry-run.
# ---------------------------------------------------------------------------


def test_deletes_refused_without_flag(tmp_path: Path) -> None:
    db = tmp_path / "k.db"
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    _seed_two_sources(db)
    _write_surviving_csvs(csv_dir, source_name="S1_NEW")  # rename + (implicit) delete of S2

    rc = sync_metadata(_args(db, csv_dir, allow_deletes=False))
    assert rc == 2  # refused — deploy.sh aborts on this
    # The safe half applied (S1 renamed); the delete did NOT happen.
    assert _scalar(db, "SELECT name FROM source WHERE id = 1") == "S1_NEW"
    assert _scalar(db, "SELECT COUNT(*) FROM source WHERE id = 2") == 1
    assert _scalar(db, "SELECT COUNT(*) FROM observation WHERE source_id = 2") == 2


def test_dry_run_changes_nothing(tmp_path: Path) -> None:
    db = tmp_path / "k.db"
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    _seed_two_sources(db)
    _write_surviving_csvs(csv_dir, source_name="S1_NEW")

    rc = sync_metadata(_args(db, csv_dir, allow_deletes=True, dry_run=True))
    assert rc == 0
    # Not even the rename landed.
    assert _scalar(db, "SELECT name FROM source WHERE id = 1") == "S1"
    assert _scalar(db, "SELECT COUNT(*) FROM source WHERE id = 2") == 1


# ---------------------------------------------------------------------------
# D — composite-PK + cascade deletes (reach + a lone junction row).
# ---------------------------------------------------------------------------


def test_reach_and_junction_cascade_deletes(tmp_path: Path) -> None:
    db = tmp_path / "k.db"
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    _schema(db)
    _exec(
        db,
        [
            "INSERT INTO state (id, name, abbreviation) VALUES (1, 'Oregon', 'OR')",
            "INSERT INTO guidebook (id, title) VALUES (1, 'Soggy Sneakers')",
            "INSERT INTO reach (id, name) VALUES (1, 'Keep Reach')",
            "INSERT INTO reach (id, name) VALUES (2, 'Drop Reach')",
            "INSERT INTO reach_state (reach_id, state_id) VALUES (1, 1)",
            "INSERT INTO reach_state (reach_id, state_id) VALUES (2, 1)",
            "INSERT INTO reach_class (id, reach_id, name) VALUES (1, 2, 'III')",
            "INSERT INTO reach_guidebook (reach_id, guidebook_id, page) VALUES (2, 1, '42')",
        ],
    )
    # CSV: keep reach 1 + its state link; drop reach 2 entirely; also drop the
    # (reach 1, state 1) link is KEPT, but write reach_state without it? No —
    # keep reach 1's link. Drop reach 2 + everything under it.
    _write_csv(csv_dir, "state", ["id", "name", "abbreviation"], [[1, "Oregon", "OR"]])
    _write_csv(csv_dir, "guidebook", ["id", "title"], [[1, "Soggy Sneakers"]])
    _write_csv(csv_dir, "reach", ["id", "name"], [[1, "Keep Reach"]])
    _write_csv(csv_dir, "reach_state", ["reach_id", "state_id"], [[1, 1]])
    _write_csv(csv_dir, "reach_class", ["id", "reach_id", "name"], [])
    _write_csv(csv_dir, "reach_guidebook", ["reach_id", "guidebook_id", "page"], [])

    rc = sync_metadata(_args(db, csv_dir, allow_deletes=True))
    assert rc == 0
    assert _rows(db, "SELECT id FROM reach ORDER BY id") == [(1,)]
    # reach 2's class + guidebook + state link all gone (cascade on the reach delete).
    assert _scalar(db, "SELECT COUNT(*) FROM reach_class") == 0
    assert _scalar(db, "SELECT COUNT(*) FROM reach_guidebook") == 0
    assert _rows(db, "SELECT reach_id, state_id FROM reach_state") == [(1, 1)]
    assert _rows(db, "PRAGMA foreign_key_check") == []


# ---------------------------------------------------------------------------
# E — a CSV diff that introduces an FK violation rolls back entirely.
# ---------------------------------------------------------------------------


def test_bad_diff_rolls_back(tmp_path: Path) -> None:
    db = tmp_path / "k.db"
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    _schema(db)
    _exec(
        db,
        [
            "INSERT INTO fetch_url (id, url) VALUES (1, 'http://fu1')",
            "INSERT INTO source (id, name, fetch_url_id) VALUES (1, 'S1', 1)",
        ],
    )
    # Inconsistent diff: point S1 at a fetch_url that doesn't exist (and isn't
    # in fetch_url.csv) → FK violation under foreign_keys=ON → rollback.
    _write_csv(csv_dir, "fetch_url", ["id", "url"], [[1, "http://fu1"]])
    _write_csv(
        csv_dir,
        "source",
        ["id", "name", "fetch_url_id"],
        [[1, "S1_RENAMED", 999]],
    )

    rc = sync_metadata(_args(db, csv_dir, allow_deletes=True))
    assert rc == 1
    # Nothing applied — the rename rolled back too.
    assert _scalar(db, "SELECT name FROM source WHERE id = 1") == "S1"
    assert _scalar(db, "SELECT fetch_url_id FROM source WHERE id = 1") == 1
