"""Round-trip tests for the metadata export + reload path.

``export_metadata.py`` snapshots the metadata tables to CSV (+ reaches.json /
reaches-gradient.json for the geometry sidecars). The reload splits in two:
``levels sync-metadata`` applies the CSV columns (by stable id, delete-safe), and
``scripts/import_metadata.py`` applies the geometry sidecars (the only path that
writes reach.geom / reach.gradient_profile — they're excluded from reach.csv). These
mutate a live DB, so the round-trip and the geom edge cases need a net.

The two scripts live outside src/, so they're loaded via importlib path (matching
test_recap.py) and driven through ``main()`` with a monkeypatched argv; the CSV apply
goes through ``kayak.cli.sync_metadata.sync_metadata`` directly.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from kayak.cli.sync_metadata import sync_metadata
from kayak.db.models import (
    Base,
    FetchUrl,
    Gauge,
    GaugeSource,
    Guidebook,
    Reach,
    ReachGuidebook,
    ReachState,
    Source,
    State,
)

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


def _sync_csvs(dst: Path, csv_dir: Path, *, allow_deletes: bool = True) -> int:
    """Apply the CSV half of a snapshot via `levels sync-metadata` (the reload path
    for everything except the geometry sidecars). Writes the minimal dataset.yaml the
    sync contract gate needs into the export dir first."""
    (csv_dir / "dataset.yaml").write_text(
        "contract_version: 1\n"
        "dataset_id: test\n"
        "name: Test dataset\n"
        "status: publishable\n"
        "license: CC-BY-NC-4.0\n"
        'engine_test_ref: "0000000000000000000000000000000000000000"\n'
    )
    return sync_metadata(
        argparse.Namespace(
            database_url=f"sqlite:///{dst}",
            csv_dir=str(csv_dir),
            allow_deletes=allow_deletes,
            allow_scaffold=False,
            dry_run=False,
            backup=False,
        )
    )


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


def test_export_excludes_generator_owned_trio(tmp_path, monkeypatch):
    """The nightly snapshot (export_metadata) must NOT write the generator-owned
    source/fetch_url/gauge_source trio even when those rows exist —
    ``levels generate-sources`` is their sole writer (dataset-separation S1's
    "no dual-writer window"). Snapshot-owned tables are still exported.

    This guards the core S1 invariant behaviorally: flipping export back to the
    full CONTRACT_CSVS set (re-introducing the dual-writer race) would fail here.
    """
    exp = _load("export_metadata")
    src, out = tmp_path / "src.db", tmp_path / "snap"
    out.mkdir()
    _make_db(src)
    eng = create_engine(f"sqlite:///{src}")
    with Session(eng) as s:
        s.add(State(id=1, name="Oregon", abbreviation="OR"))
        s.add(Gauge(id=1, name="G"))
        s.add(FetchUrl(id=1, url="https://example.com/f", parser="nwps", is_active=True))
        s.add(Source(id=1, name="STN", fetch_url_id=1))
        s.flush()
        s.add(GaugeSource(gauge_id=1, source_id=1))
        s.commit()
    eng.dispose()

    monkeypatch.setattr(sys, "argv", ["export_metadata", "--db", str(src), "--out", str(out)])
    assert exp.main() == 0

    # Generator-owned trio: present in the DB, but the snapshot must not write it.
    for stem in ("source", "fetch_url", "gauge_source"):
        assert not (out / f"{stem}.csv").exists(), f"snapshot must not export {stem}.csv"
    # Snapshot-owned tables are still exported.
    assert (out / "state.csv").exists()
    assert (out / "gauge.csv").exists()


def test_round_trip_preserves_geom(tmp_path, monkeypatch):
    """export → fresh DB reload (sync-metadata CSV + import_metadata sidecar)
    reproduces the reach rows, with geom carried through reaches.json (and genuinely
    excluded from reach.csv)."""
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

    # CSV columns via sync-metadata; geom via the import_metadata sidecar applier.
    assert _sync_csvs(dst, out) == 0
    monkeypatch.setattr(sys, "argv", ["import_metadata", "--db", str(dst), "--in", str(out)])
    assert imp.main() == 0

    assert _reach_rows(dst) == {
        1: ("Alpha", "-121.0 44.0,-121.1 44.1"),
        2: ("Bravo", "-122.0 45.0"),
        3: ("Charlie", None),
    }


def test_export_rounds_coordinates_to_six_dp(tmp_path, monkeypatch):
    """export quantizes Numeric(9,6) coordinates to 6 dp so the CSV conforms to
    the declared scale (the DB stores full-precision floats from NHD traces)."""
    exp = _load("export_metadata")
    src, out = tmp_path / "src.db", tmp_path / "snap"
    out.mkdir()
    _make_db(src)
    _seed_reaches(
        src,
        [{"id": 1, "name": "HiPrec", "latitude": 44.3859538093346, "longitude": -123.831778038249}],
    )
    monkeypatch.setattr(sys, "argv", ["export_metadata", "--db", str(src), "--out", str(out)])
    assert exp.main() == 0
    text = (out / "reach.csv").read_text()
    assert "44.385954" in text and "-123.831778" in text
    assert "44.3859538093346" not in text  # full-precision float dropped


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


def test_import_fails_loud_on_unmatched_sidecar_reach(tmp_path, monkeypatch):
    """A sidecar reach id with no DB row (the classic "ran before sync-metadata" /
    wrong-DB mistake) fails loud and rolls back the whole apply; --allow-missing-reaches
    opts into a partial apply of the ids that DO match."""
    imp = _load("import_metadata")
    dst, snap = tmp_path / "dst.db", tmp_path / "snap"
    snap.mkdir()
    _make_db(dst)
    _seed_reaches(dst, [{"id": 1, "name": "here"}])  # reach 1 exists; 99 does not
    (snap / "reaches.json").write_text(json.dumps({"1": "G1 geom", "99": "G99 geom"}))

    monkeypatch.setattr(sys, "argv", ["import_metadata", "--db", str(dst), "--in", str(snap)])
    assert imp.main() == 1  # reach 99 has no row → fail loud
    assert _reach_rows(dst) == {1: ("here", None)}  # rolled back: reach 1's geom NOT applied

    monkeypatch.setattr(
        sys,
        "argv",
        ["import_metadata", "--db", str(dst), "--in", str(snap), "--allow-missing-reaches"],
    )
    assert imp.main() == 0  # partial apply explicitly allowed
    assert _reach_rows(dst) == {1: ("here", "G1 geom")}  # reach 1 applied; 99 skipped


def test_csv_apply_preserves_geom_absent_from_snapshot(tmp_path, monkeypatch):
    """A reach carrying geom in the live DB but absent from reaches.json keeps its
    geom across a CSV apply: geom is excluded from reach.csv, so sync-metadata's upsert
    never touches it (and the EXCLUDED-vs-OPTIONAL split means it isn't reset either)."""
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

    # CSV apply (sync-metadata): inserts reach 1, renames reach 2 — geom untouched.
    assert _sync_csvs(dst, out) == 0
    monkeypatch.setattr(sys, "argv", ["import_metadata", "--db", str(dst), "--in", str(out)])
    assert imp.main() == 0  # sidecar applies reach 1's geom from reaches.json

    rows = _reach_rows(dst)
    assert rows[1] == ("snap1", "-120.0 43.0")  # inserted + geom from reaches.json
    assert rows[2] == ("snap2", "-99.9 38.0 LIVE")  # name updated, live geom preserved


def test_resync_idempotent_across_pk_shapes(tmp_path, monkeypatch):
    """A second CSV apply into a populated DB succeeds — exercising the composite-PK
    conflict paths the single-reach tests don't: reach_state (all columns are PK → ON
    CONFLICT DO NOTHING) and reach_guidebook (has non-PK columns → ON CONFLICT DO
    UPDATE). sync-metadata's upsert runs every apply, so the second hits the conflicts."""
    exp = _load("export_metadata")
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

    assert _sync_csvs(dst, out) == 0  # first apply inserts
    assert _sync_csvs(dst, out) == 0  # second apply hits the DO NOTHING + DO UPDATE paths

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

    assert _sync_csvs(dst, out) == 0  # CSV columns via sync-metadata
    monkeypatch.setattr(sys, "argv", ["import_metadata", "--db", str(dst), "--in", str(out)])
    assert imp.main() == 0  # gradient via the import_metadata sidecar applier
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
