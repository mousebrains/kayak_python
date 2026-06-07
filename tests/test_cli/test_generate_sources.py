"""Tests for ``levels generate-sources`` (dataset-separation S1, expand phase).

The load-bearing invariant is the **byte round-trip**: ``generate-sources`` must
reproduce the committed ``source.csv`` + ``fetch_url.csv`` from the authoritative
``sources.yaml`` exactly, so the registry is a complete, drift-proof projection
of those two CSVs (if a source can't round-trip, the format is missing a field).
These exercise that against the committed fixture, the validation rules, and the
column-order preservation that keeps the round-trip stable against the prod
snapshot's physical-``PRAGMA``-order CSVs (``source.timezone`` is an
``ALTER``-added column, so it lands last on a migrated DB — not at its model
position).
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pytest

from kayak.cli import generate_sources as gs
from kayak.dataset import layout

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "dataset"


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    dst = tmp_path / "dataset"
    shutil.copytree(FIXTURE, dst)
    return dst


def _ns(dir: Path, *, check: bool = False, from_csv: bool = False) -> argparse.Namespace:
    return argparse.Namespace(dir=str(dir), check=check, from_csv=from_csv)


def _counters(dir: Path, **kw: int) -> None:
    lines = ["table,next_id", *(f"{t},{n}" for t, n in kw.items())]
    (dir / "id_counters.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _calc(dir: Path, *ids: int) -> None:
    lines = ["id,data_type,expression,time_expression,note,provenance_slug"]
    lines += [f"{i},flow,x,,," for i in ids]
    (dir / "calc_expression.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- the round-trip invariant -------------------------------------------------


def test_generate_reproduces_fixture_byte_for_byte(dataset: Path) -> None:
    before = {n: (dataset / n).read_bytes() for n in ("source.csv", "fetch_url.csv")}
    gs.generate(dataset)
    after = {n: (dataset / n).read_bytes() for n in ("source.csv", "fetch_url.csv")}
    assert after == before


def test_check_passes_on_committed_fixture() -> None:
    # Read-only against the committed fixture: the registry generates exactly it.
    assert gs._main(_ns(FIXTURE, check=True)) == 0


def test_check_fails_on_hand_edited_csv(dataset: Path) -> None:
    # Editing a CSV cell without updating sources.yaml must trip --check (the CI
    # gate's whole purpose: the CSVs are generated artifacts, not hand-editable).
    src = dataset / "source.csv"
    src.write_text(src.read_text(encoding="utf-8").replace("USGS", "TYPO"), encoding="utf-8")
    assert gs._main(_ns(dataset, check=True)) == 1


def test_reverse_engineer_then_generate_round_trips(dataset: Path) -> None:
    original = {n: (dataset / n).read_bytes() for n in ("source.csv", "fetch_url.csv")}
    (dataset / "sources.yaml").unlink()
    gs.reverse_engineer(dataset)
    gs.generate(dataset)
    assert {n: (dataset / n).read_bytes() for n in ("source.csv", "fetch_url.csv")} == original


# --- column-order handling ----------------------------------------------------


def test_preserves_committed_pragma_column_order(tmp_path: Path) -> None:
    # A migrated-DB export puts the ALTER-added `timezone` LAST (not at its model
    # position). generate-sources must preserve that committed order so --check
    # never flags the benign, non-semantic difference.
    d = tmp_path / "ds"
    d.mkdir()
    pragma_source = (
        "id,name,agency,fetch_url_id,calc_expression_id,timezone\n"
        "1,STAW1,USBR,1,,America/Los_Angeles\n"
    )
    (d / "source.csv").write_text(pragma_source, encoding="utf-8")
    (d / "fetch_url.csv").write_text(
        "id,url,parser,hours,is_active\n1,https://example/x,nwps,,1\n", encoding="utf-8"
    )
    _counters(d, source=2, fetch_url=2)
    gs.reverse_engineer(d)
    gs.generate(d)
    assert (d / "source.csv").read_text(encoding="utf-8") == pragma_source


def test_absent_csv_falls_back_to_model_order(tmp_path: Path) -> None:
    # A brand-new dataset (no committed CSV) gets the canonical model column order.
    d = tmp_path / "ds"
    d.mkdir()
    _counters(d, source=2, fetch_url=2)
    (d / "sources.yaml").write_text(
        "fetch_urls:\n"
        "- {id: 1, url: 'https://example/x', parser: nwps, enabled: true}\n"
        "sources:\n"
        "- {id: 1, name: STAW1, agency: USBR, fetch_url_id: 1}\n",
        encoding="utf-8",
    )
    gs.generate(d)
    header = (d / "source.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header == ",".join(layout.ordered_columns("source"))


def test_drifted_header_is_rejected(tmp_path: Path) -> None:
    # A committed header whose column set diverges from the schema is corruption,
    # not an order to preserve — reject it rather than silently propagate.
    d = tmp_path / "ds"
    d.mkdir()
    (d / "source.csv").write_text("id,name,agency,STRAY\n1,X,USGS,z\n", encoding="utf-8")
    (d / "fetch_url.csv").write_text("id,url,parser,hours,is_active\n", encoding="utf-8")
    _counters(d, source=2, fetch_url=2)
    (d / "sources.yaml").write_text(
        "sources:\n- {id: 1, name: X, agency: USGS}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="does not match the schema"):
        gs.generate(d)
    # ...and via --check it surfaces as a clean exit 1, not an uncaught traceback.
    assert gs._main(_ns(d, check=True)) == 1


# --- validation rules ---------------------------------------------------------


def _valid_meta() -> dict:
    return {
        "fetch_urls": [{"id": 1, "url": "https://example/x", "parser": "nwps", "enabled": True}],
        "sources": [
            {"id": 1, "name": "A", "agency": "USGS"},
            {"id": 2, "name": "B", "agency": "NWS", "fetch_url_id": 1},
            {"id": 3, "name": "C", "agency": "Calculation", "calc_expression_id": 1},
        ],
    }


@pytest.fixture
def vdir(tmp_path: Path) -> Path:
    d = tmp_path / "ds"
    d.mkdir()
    _counters(d, source=99, fetch_url=99)
    _calc(d, 1)
    return d


def test_valid_registry_has_no_problems(vdir: Path) -> None:
    assert gs.validate_registry(_valid_meta(), vdir) == []


def test_duplicate_source_id(vdir: Path) -> None:
    meta = _valid_meta()
    meta["sources"][1]["id"] = 1
    assert any("duplicate source id" in p for p in gs.validate_registry(meta, vdir))


def test_duplicate_fetch_url_id(vdir: Path) -> None:
    meta = _valid_meta()
    meta["fetch_urls"].append({"id": 1, "url": "https://example/y", "parser": "nwps"})
    assert any("duplicate fetch_url id" in p for p in gs.validate_registry(meta, vdir))


def test_unknown_parser(vdir: Path) -> None:
    meta = _valid_meta()
    meta["fetch_urls"][0]["parser"] = "bogus"
    assert any("unknown parser" in p for p in gs.validate_registry(meta, vdir))


def test_both_refs_rejected(vdir: Path) -> None:
    meta = _valid_meta()
    meta["sources"][1]["calc_expression_id"] = 1  # already has fetch_url_id
    assert any("at most one of" in p for p in gs.validate_registry(meta, vdir))


def test_dangling_fetch_url_id(vdir: Path) -> None:
    meta = _valid_meta()
    meta["sources"][1]["fetch_url_id"] = 42
    assert any("fetch_url_id 42 not defined" in p for p in gs.validate_registry(meta, vdir))


def test_dangling_calc_expression_id(vdir: Path) -> None:
    meta = _valid_meta()
    meta["sources"][2]["calc_expression_id"] = 42
    assert any("not in calc_expression.csv" in p for p in gs.validate_registry(meta, vdir))


def test_missing_required_field(vdir: Path) -> None:
    meta = _valid_meta()
    del meta["sources"][0]["name"]
    del meta["fetch_urls"][0]["url"]
    problems = gs.validate_registry(meta, vdir)
    assert any("missing required field 'name'" in p for p in problems)
    assert any("missing required field 'url'" in p for p in problems)


def test_quoted_id_rejected(vdir: Path) -> None:
    # A YAML-quoted id ("1") must not alias the int id 1 (would collide in the CSV).
    meta = _valid_meta()
    meta["sources"][0]["id"] = "1"
    assert any("id must be an integer" in p for p in gs.validate_registry(meta, vdir))


def test_quoted_ref_rejected(vdir: Path) -> None:
    meta = _valid_meta()
    meta["sources"][1]["fetch_url_id"] = "1"
    assert any("fetch_url_id must be an integer" in p for p in gs.validate_registry(meta, vdir))


def test_bool_id_rejected(vdir: Path) -> None:
    # bool is an int subclass — guard against `id: true` slipping through.
    meta = _valid_meta()
    meta["fetch_urls"][0]["id"] = True
    assert any("id must be an integer" in p for p in gs.validate_registry(meta, vdir))


def test_id_at_or_above_next_id(tmp_path: Path) -> None:
    d = tmp_path / "ds"
    d.mkdir()
    _counters(d, source=2, fetch_url=99)  # source next_id=2, so id 3 is stale
    _calc(d, 1)
    assert any("stale counter" in p for p in gs.validate_registry(_valid_meta(), d))


def test_comma_hours_round_trips(tmp_path: Path) -> None:
    # A multi-hour fetch_url ("6,12,18") must survive CSV -> sources.yaml -> CSV;
    # reverse_engineer must not int()-cast it. (The documented --from-csv path.)
    d = tmp_path / "ds"
    d.mkdir()
    source_csv = "id,name,agency,timezone,fetch_url_id,calc_expression_id\n1,STAW1,USBR,,1,\n"
    fetch_csv = 'id,url,parser,hours,is_active\n1,https://example/x,nwps,"6,12,18",1\n'
    (d / "source.csv").write_text(source_csv, encoding="utf-8")
    (d / "fetch_url.csv").write_text(fetch_csv, encoding="utf-8")
    _counters(d, source=2, fetch_url=2)
    gs.reverse_engineer(d)
    assert "hours: 6,12,18" in (d / "sources.yaml").read_text(encoding="utf-8")
    gs.generate(d)
    assert (d / "fetch_url.csv").read_text(encoding="utf-8") == fetch_csv


def test_check_missing_csv_reports_cleanly(tmp_path: Path) -> None:
    # --check with no committed CSV must exit 1 with a message, not traceback.
    d = tmp_path / "ds"
    d.mkdir()
    _counters(d, source=2, fetch_url=2)
    (d / "sources.yaml").write_text(
        "sources:\n- {id: 1, name: X, agency: USGS}\n", encoding="utf-8"
    )
    assert gs._main(_ns(d, check=True)) == 1
