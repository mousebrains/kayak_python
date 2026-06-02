"""Round-trip tests for scripts/export_metadata.py + scripts/import_metadata.py.

These scripts snapshot the metadata tables to CSV (+ reaches.json for geom) and
read them back — the supported prod rebuild/recovery path (docs/migrations.md)
and the only way committed geometry reaches prod. They mutate a live DB, so the
round-trip and the geom edge cases need a net.

The scripts live outside src/, so they're loaded via importlib path (matching
test_recap.py). Each is driven through ``main()`` with a monkeypatched argv.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from kayak.db.models import Base, Guidebook, Reach, ReachGuidebook, ReachState, State

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_db(path: Path) -> None:
    """Create an empty kayak schema at `path`."""
    eng = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(eng)
    eng.dispose()


def _seed_reaches(path: Path, reaches: list[dict]) -> None:
    eng = create_engine(f"sqlite:///{path}")
    with Session(eng) as s:
        for r in reaches:
            s.add(Reach(**r))
        s.commit()
    eng.dispose()


def _reach_rows(path: Path) -> dict[int, tuple[str | None, str | None]]:
    """{id: (name, geom)} for every reach, so a test can assert both the
    CSV-managed column and the reaches.json-managed geom in one shot."""
    eng = create_engine(f"sqlite:///{path}")
    with eng.connect() as c:
        rows = c.execute(text("SELECT id, name, geom FROM reach ORDER BY id")).all()
    eng.dispose()
    return {r[0]: (r[1], r[2]) for r in rows}


def test_round_trip_preserves_geom(tmp_path, monkeypatch):
    """export → fresh DB import reproduces the reach rows, with geom carried
    through reaches.json (and genuinely excluded from reach.csv)."""
    exp = _load("export_metadata")
    imp = _load("import_metadata")
    src, dst, out = tmp_path / "src.db", tmp_path / "dst.db", tmp_path / "snap"
    out.mkdir()
    _make_db(src)
    _make_db(dst)
    _seed_reaches(
        src,
        [
            {"id": 1, "name": "Alpha", "geom": "-121.0 44.0,-121.1 44.1"},
            {"id": 2, "name": "Bravo", "geom": "-122.0 45.0"},
            {"id": 3, "name": "Charlie"},  # no geom
        ],
    )

    monkeypatch.setattr(sys, "argv", ["export_metadata", "--db", str(src), "--out", str(out)])
    assert exp.main() == 0
    assert (out / "reaches.json").exists()
    header = (out / "reach.csv").read_text().splitlines()[0]
    assert "geom" not in header.split(",")

    monkeypatch.setattr(sys, "argv", ["import_metadata", "--db", str(dst), "--in", str(out)])
    assert imp.main() == 0

    assert _reach_rows(dst) == {
        1: ("Alpha", "-121.0 44.0,-121.1 44.1"),
        2: ("Bravo", "-122.0 45.0"),
        3: ("Charlie", None),
    }


def test_geom_only_applies_geom_leaves_metadata(tmp_path, monkeypatch):
    """--geom-only applies reaches.json to existing rows and touches nothing
    else (skips the CSV upsert entirely)."""
    imp = _load("import_metadata")
    dst, snap = tmp_path / "dst.db", tmp_path / "snap"
    snap.mkdir()
    _make_db(dst)
    _seed_reaches(dst, [{"id": 1, "name": "orig1"}, {"id": 2, "name": "orig2"}])
    (snap / "reaches.json").write_text(json.dumps({"1": "G1 g1", "2": "G2 g2"}))

    monkeypatch.setattr(
        sys, "argv", ["import_metadata", "--db", str(dst), "--in", str(snap), "--geom-only"]
    )
    assert imp.main() == 0

    assert _reach_rows(dst) == {1: ("orig1", "G1 g1"), 2: ("orig2", "G2 g2")}


def test_full_import_preserves_geom_absent_from_snapshot(tmp_path, monkeypatch):
    """Regression for the UPSERT fix: a reach carrying geom in the live DB but
    absent from reaches.json keeps its geom on a full import. INSERT OR REPLACE
    used to delete+reinsert the row and null the un-snapshotted geom column."""
    exp = _load("export_metadata")
    imp = _load("import_metadata")
    src, dst, out = tmp_path / "src.db", tmp_path / "dst.db", tmp_path / "snap"
    out.mkdir()
    _make_db(src)
    _make_db(dst)
    # Snapshot: reach 1 has geom, reach 2 does not → reaches.json = {"1": ...}.
    _seed_reaches(
        src,
        [{"id": 1, "name": "snap1", "geom": "-120.0 43.0"}, {"id": 2, "name": "snap2"}],
    )
    monkeypatch.setattr(sys, "argv", ["export_metadata", "--db", str(src), "--out", str(out)])
    assert exp.main() == 0
    assert json.loads((out / "reaches.json").read_text()) == {"1": "-120.0 43.0"}

    # Live dst: reach 2 already carries geom the snapshot doesn't know about.
    _seed_reaches(dst, [{"id": 2, "name": "live2", "geom": "-99.9 38.0 LIVE"}])

    monkeypatch.setattr(sys, "argv", ["import_metadata", "--db", str(dst), "--in", str(out)])
    assert imp.main() == 0

    rows = _reach_rows(dst)
    assert rows[1] == ("snap1", "-120.0 43.0")  # inserted + geom from reaches.json
    assert rows[2] == ("snap2", "-99.9 38.0 LIVE")  # name updated, live geom preserved


def test_reimport_idempotent_across_pk_shapes(tmp_path, monkeypatch):
    """A second full import into a populated DB succeeds — exercising the
    composite-PK conflict paths the single-reach tests don't: reach_state (all
    columns are PK → ON CONFLICT DO NOTHING) and reach_guidebook (has non-PK
    columns → ON CONFLICT DO UPDATE)."""
    exp = _load("export_metadata")
    imp = _load("import_metadata")
    src, dst, out = tmp_path / "src.db", tmp_path / "dst.db", tmp_path / "snap"
    out.mkdir()
    _make_db(src)
    _make_db(dst)

    eng = create_engine(f"sqlite:///{src}")
    with Session(eng) as s:
        s.add_all(
            [
                Reach(id=1, name="Alpha"),
                State(id=1, name="Oregon", abbreviation="OR"),
                Guidebook(id=1, title="Soggy Sneakers"),
                ReachState(reach_id=1, state_id=1),
                ReachGuidebook(reach_id=1, guidebook_id=1, page="p1"),
            ]
        )
        s.commit()
    eng.dispose()

    monkeypatch.setattr(sys, "argv", ["export_metadata", "--db", str(src), "--out", str(out)])
    assert exp.main() == 0

    monkeypatch.setattr(sys, "argv", ["import_metadata", "--db", str(dst), "--in", str(out)])
    assert imp.main() == 0  # first import inserts
    assert imp.main() == 0  # second import hits the DO NOTHING + DO UPDATE conflict paths

    eng = create_engine(f"sqlite:///{dst}")
    with eng.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM reach_state")).scalar() == 1
        page = c.execute(text("SELECT page FROM reach_guidebook WHERE reach_id = 1")).scalar()
    eng.dispose()
    assert page == "p1"  # DO UPDATE re-applied the non-PK column cleanly


def _reach_gradients(path: Path) -> dict[int, str | None]:
    """{id: gradient_profile} — the reaches-gradient.json-managed column (R6.1)."""
    eng = create_engine(f"sqlite:///{path}")
    with eng.connect() as c:
        rows = c.execute(text("SELECT id, gradient_profile FROM reach ORDER BY id")).all()
    eng.dispose()
    return {r[0]: r[1] for r in rows}


def test_round_trip_preserves_gradient(tmp_path, monkeypatch):
    """gradient_profile rides through reaches-gradient.json, excluded from reach.csv."""
    exp = _load("export_metadata")
    imp = _load("import_metadata")
    src, dst, out = tmp_path / "src.db", tmp_path / "dst.db", tmp_path / "snap"
    out.mkdir()
    _make_db(src)
    _make_db(dst)
    _seed_reaches(
        src,
        [
            {"id": 1, "name": "Alpha", "gradient_profile": '{"samples":[1,2,3]}'},
            {"id": 2, "name": "Bravo"},  # no gradient
        ],
    )

    monkeypatch.setattr(sys, "argv", ["export_metadata", "--db", str(src), "--out", str(out)])
    assert exp.main() == 0
    assert json.loads((out / "reaches-gradient.json").read_text()) == {"1": '{"samples":[1,2,3]}'}
    header = (out / "reach.csv").read_text().splitlines()[0]
    assert "gradient_profile" not in header.split(",")

    monkeypatch.setattr(sys, "argv", ["import_metadata", "--db", str(dst), "--in", str(out)])
    assert imp.main() == 0
    assert _reach_gradients(dst) == {1: '{"samples":[1,2,3]}', 2: None}


def test_gradient_only_applies_gradient_leaves_metadata(tmp_path, monkeypatch):
    """--gradient-only applies reaches-gradient.json to existing rows and skips
    the CSV upsert (and the geom apply)."""
    imp = _load("import_metadata")
    dst, snap = tmp_path / "dst.db", tmp_path / "snap"
    snap.mkdir()
    _make_db(dst)
    _seed_reaches(dst, [{"id": 1, "name": "orig1"}, {"id": 2, "name": "orig2"}])
    (snap / "reaches-gradient.json").write_text(json.dumps({"1": "GP1", "2": "GP2"}))

    monkeypatch.setattr(
        sys, "argv", ["import_metadata", "--db", str(dst), "--in", str(snap), "--gradient-only"]
    )
    assert imp.main() == 0

    assert _reach_gradients(dst) == {1: "GP1", 2: "GP2"}
    eng = create_engine(f"sqlite:///{dst}")
    with eng.connect() as c:
        names = dict(c.execute(text("SELECT id, name FROM reach ORDER BY id")).all())
    eng.dispose()
    assert names == {1: "orig1", 2: "orig2"}  # names untouched (CSV upsert skipped)


def test_geom_and_gradient_only_together_applies_both_skips_csv(tmp_path, monkeypatch):
    """Passing BOTH --geom-only and --gradient-only applies both JSON blobs and
    still skips the CSV upsert — it must NOT be the silent no-op the original
    ``if not the_other_flag`` guards produced (each branch cancelled the other)."""
    imp = _load("import_metadata")
    dst, snap = tmp_path / "dst.db", tmp_path / "snap"
    snap.mkdir()
    _make_db(dst)
    _seed_reaches(dst, [{"id": 1, "name": "orig1"}, {"id": 2, "name": "orig2"}])
    (snap / "reaches.json").write_text(json.dumps({"1": "G1 g1", "2": "G2 g2"}))
    (snap / "reaches-gradient.json").write_text(json.dumps({"1": "GP1", "2": "GP2"}))

    monkeypatch.setattr(
        sys,
        "argv",
        ["import_metadata", "--db", str(dst), "--in", str(snap), "--geom-only", "--gradient-only"],
    )
    assert imp.main() == 0

    assert _reach_rows(dst) == {1: ("orig1", "G1 g1"), 2: ("orig2", "G2 g2")}  # geom applied
    assert _reach_gradients(dst) == {1: "GP1", 2: "GP2"}  # gradient applied
    # names untouched ⇒ CSV upsert was skipped (no *.csv in snap dir to load anyway)
