"""Guard: the committed reach snapshot passes ``levels check-reaches`` cleanly.

``reach.geom`` and ``reach.gradient_profile`` are dev-only-regenerable (the
DEM/NHD trace stack doesn't exist on prod), so they ride to prod as committed
snapshots — ``data/db/reaches.json`` (geom) and ``data/db/reaches-gradient.json``
(gradient_profile) — applied by ``scripts/import_metadata.py``. The only thing
validating them today is the prod pipeline's ``check-reaches`` soft-fail, which
fires *after* a bad snapshot has already deployed. This test runs the same
validator (:func:`kayak.cli.check_reaches.scan_for_issues`) against the committed
snapshot at merge time, so a hand-broken geom endpoint, an out-of-range
coordinate, an endpoint-column drift, or an extreme gradient peak is caught in CI
instead of on prod.

Mechanism (load-bearing): ``scan_for_issues(database_url=…)`` opens its *own*
engine via ``get_session(url)`` -> ``create_engine(url)``. A conftest in-memory
``:memory:`` engine is unreachable from that second ``create_engine`` (it opens a
separate, empty DB -> false green), so this test materializes the snapshot into a
temp **file** DB and points ``scan_for_issues`` at it by URL. We load the geom
*and* the gradient snapshot so the extreme-peak check (``check_reaches.py``)
exercises real data, matching what prod's ``check-reaches`` step sees.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from types import ModuleType

from sqlalchemy import create_engine

from kayak.cli.check_reaches import scan_for_issues
from kayak.db.models import Base

REPO_DIR = Path(__file__).resolve().parents[1]
DATA_DB_DIR = REPO_DIR / "data" / "db"
SCRIPTS_DIR = REPO_DIR / "scripts"

# The committed snapshot the loaders read. Asserted explicitly so the test
# fails loudly (rather than vacuously passing on an empty DB) if the snapshot
# ever shrinks or the fixtures move.
EXPECTED_REACH_COUNT = 420


def _load_import_metadata() -> ModuleType:
    """Load ``scripts/import_metadata.py`` as a module (it lives outside src/,
    so it's loaded by path — matching ``test_metadata_roundtrip.py``)."""
    spec = importlib.util.spec_from_file_location(
        "import_metadata", SCRIPTS_DIR / "import_metadata.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_committed_reach_snapshot_passes_check_reaches(tmp_path) -> None:
    db_path = tmp_path / "kayak.db"

    # (1) Create the schema on a real file DB (scan_for_issues opens its own
    # engine on the URL, so an in-memory DB would be invisible to it).
    eng = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(eng)
    eng.dispose()

    # (2) Load the committed snapshot with import_metadata's own loaders against
    # a raw sqlite3 connection: CSV rows (reach.csv + the rest) via _load_csvs,
    # then geom via _apply_geom and gradient_profile via _apply_gradient (the
    # gradient load exercises the extreme-peak check in check_reaches).
    imp = _load_import_metadata()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        with conn:
            imp._load_csvs(conn, DATA_DB_DIR)
            imp._apply_geom(conn, DATA_DB_DIR)
            imp._apply_gradient(conn, DATA_DB_DIR)
    finally:
        conn.close()

    # (3) Run the real validator against the materialized DB by URL.
    total, flagged = scan_for_issues(database_url=f"sqlite:///{db_path}")

    # The snapshot must have actually loaded — guards against a vacuous green
    # if the loaders silently no-op'd (wrong dir, empty CSV, etc.).
    assert total == EXPECTED_REACH_COUNT, (
        f"expected {EXPECTED_REACH_COUNT} reaches from the committed snapshot, "
        f"loaded {total} — did the snapshot or fixtures move?"
    )
    assert flagged == [], (
        "the committed reach snapshot fails check-reaches — fix the geom/"
        "gradient snapshot (or the start/end columns) before merge:\n"
        + "\n".join(f"{label}\n  - " + "\n  - ".join(issues) for label, issues in flagged)
    )
