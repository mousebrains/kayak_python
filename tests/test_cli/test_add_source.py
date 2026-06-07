"""Tests for ``levels add-source`` (dataset-separation S1-registry-b).

`add-source` appends a validated source (and optional new fetch_url) to a dataset's
`sources.yaml`, allocates its stable id(s) from `id_counters.csv`, bumps the
counter(s), and regenerates source.csv + fetch_url.csv — atomically, preserving the
byte round-trip invariant (`generate-sources --check` still passes after). These
exercise the three source shapes, id allocation, the add-source-specific guards,
usage errors, atomic failure isolation, and the reverse_engineer-serializer refactor.
"""

from __future__ import annotations

import argparse
import csv
import shutil
from pathlib import Path

import pytest

from kayak.cli import generate_sources as gs

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "dataset"


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    dst = tmp_path / "dataset"
    shutil.copytree(FIXTURE, dst)
    return dst


def _ns_add(d: Path, **kw: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "dir": str(d),
        "name": "NEWW1",
        "agency": None,
        "timezone": None,
        "url": None,
        "parser": None,
        "hours": None,
        "disabled": False,
        "calc_expression_id": None,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def _rows(d: Path, table: str) -> list[dict[str, str]]:
    with (d / f"{table}.csv").open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _counter(d: Path, table: str) -> int:
    with (d / "id_counters.csv").open(encoding="utf-8") as fh:
        return next(int(r["next_id"]) for r in csv.DictReader(fh) if r["table"] == table)


def _snapshot(d: Path) -> dict[str, bytes]:
    files = ("sources.yaml", "id_counters.csv", "source.csv", "fetch_url.csv")
    return {n: (d / n).read_bytes() for n in files if (d / n).is_file()}


# --- happy paths (one per source shape) ---------------------------------------


def test_add_fetch_backed_source(dataset: Path) -> None:
    allocated = gs.add_source(
        dataset, name="NEWW1", agency="NWS", url="https://example/new", parser="nwps", hours="6,18"
    )
    assert allocated == {"source": 4, "fetch_url": 2}
    assert _counter(dataset, "source") == 5
    assert _counter(dataset, "fetch_url") == 3
    src = {r["name"]: r for r in _rows(dataset, "source")}["NEWW1"]
    assert src["id"] == "4" and src["agency"] == "NWS" and src["fetch_url_id"] == "2"
    fu = {r["id"]: r for r in _rows(dataset, "fetch_url")}["2"]
    assert fu["url"] == "https://example/new" and fu["parser"] == "nwps" and fu["hours"] == "6,18"
    assert fu["is_active"] == "1"
    assert gs._main(argparse.Namespace(dir=str(dataset), check=True, from_csv=False)) == 0


def test_add_calc_backed_source(dataset: Path) -> None:
    allocated = gs.add_source(dataset, name="NewCalc", agency="Calculation", calc_expression_id=1)
    assert allocated == {"source": 4}
    assert _counter(dataset, "source") == 5
    assert _counter(dataset, "fetch_url") == 2  # untouched
    src = {r["name"]: r for r in _rows(dataset, "source")}["NewCalc"]
    assert src["calc_expression_id"] == "1" and src["fetch_url_id"] == ""


def test_add_detached_usgs_ogc_source(dataset: Path) -> None:
    allocated = gs.add_source(dataset, name="12345678", agency="USGS")
    assert allocated == {"source": 4}
    src = {r["name"]: r for r in _rows(dataset, "source")}["12345678"]
    assert src["fetch_url_id"] == "" and src["calc_expression_id"] == ""


def test_disabled_fetch_url(dataset: Path) -> None:
    gs.add_source(dataset, name="OFFW1", url="https://example/off", parser="nwps", enabled=False)
    fu = {r["url"]: r for r in _rows(dataset, "fetch_url")}["https://example/off"]
    assert fu["is_active"] == "0"


# --- allocation / counter bookkeeping -----------------------------------------


def test_counter_row_order_preserved(dataset: Path) -> None:
    before = (dataset / "id_counters.csv").read_text(encoding="utf-8").splitlines()
    gs.add_source(dataset, name="NEWW1", url="https://example/x", parser="nwps")
    after = (dataset / "id_counters.csv").read_text(encoding="utf-8").splitlines()
    # Same rows in the same order; only the source/fetch_url value cells changed.
    assert [ln.split(",")[0] for ln in after] == [ln.split(",")[0] for ln in before]
    changed = {a.split(",")[0] for a, b in zip(after, before, strict=True) if a != b}
    assert changed == {"source", "fetch_url"}


# --- add-source-specific guards (reject before any write) ---------------------


def test_duplicate_name_rejected(dataset: Path) -> None:
    snap = _snapshot(dataset)
    with pytest.raises(ValueError, match="already exists"):
        gs.add_source(dataset, name="FXTW1")  # FXTW1 is in the fixture
    assert _snapshot(dataset) == snap


def test_duplicate_url_rejected(dataset: Path) -> None:
    existing = _rows(dataset, "fetch_url")[0]["url"]
    snap = _snapshot(dataset)
    with pytest.raises(ValueError, match="url .* already exists"):
        gs.add_source(dataset, name="DUPW1", url=existing, parser="nwps")
    assert _snapshot(dataset) == snap


def test_calc_id_must_exist(dataset: Path) -> None:
    snap = _snapshot(dataset)
    with pytest.raises(ValueError, match="not in calc_expression.csv"):
        gs.add_source(dataset, name="BadCalc", calc_expression_id=42)
    assert _snapshot(dataset) == snap


def test_calc_id_rejected_when_no_calc_csv(dataset: Path) -> None:
    (dataset / "calc_expression.csv").unlink()
    with pytest.raises(ValueError, match="calc_expression.csv not present"):
        gs.add_source(dataset, name="BadCalc", calc_expression_id=1)


def test_missing_counter_row_rejected(tmp_path: Path) -> None:
    d = tmp_path / "ds"
    d.mkdir()
    (d / "sources.yaml").write_text("fetch_urls: []\nsources: []\n", encoding="utf-8")
    (d / "id_counters.csv").write_text(
        "table,next_id\nfetch_url,2\n", encoding="utf-8"
    )  # no source
    with pytest.raises(ValueError, match="no row for table 'source'"):
        gs.add_source(d, name="X")


def test_missing_sources_yaml_rejected(tmp_path: Path) -> None:
    d = tmp_path / "ds"
    d.mkdir()
    (d / "id_counters.csv").write_text("table,next_id\nsource,4\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing sources.yaml"):
        gs.add_source(d, name="X")


# --- atomicity / failure isolation --------------------------------------------


def test_validation_failure_leaves_files_untouched(dataset: Path) -> None:
    snap = _snapshot(dataset)
    with pytest.raises(ValueError, match="not a valid IANA timezone"):
        gs.add_source(dataset, name="TZW1", timezone="Mars/Phobos")
    assert _snapshot(dataset) == snap


# --- round-trip invariants ----------------------------------------------------


def test_round_trip_holds_after_add(dataset: Path) -> None:
    gs.add_source(dataset, name="NEWW1", url="https://example/rt", parser="nwps")
    # The regenerated CSVs reverse-engineer back to the on-disk sources.yaml byte-for-byte.
    after_yaml = (dataset / "sources.yaml").read_bytes()
    gs.reverse_engineer(dataset)
    assert (dataset / "sources.yaml").read_bytes() == after_yaml


# --- CLI glue: usage errors (exit 2) ------------------------------------------


def test_url_without_parser_is_usage_error(dataset: Path) -> None:
    assert gs._add_source_main(_ns_add(dataset, url="https://example/x")) == 2


def test_parser_without_url_is_usage_error(dataset: Path) -> None:
    assert gs._add_source_main(_ns_add(dataset, parser="nwps")) == 2


def test_fetch_and_calc_mutually_exclusive(dataset: Path) -> None:
    ns = _ns_add(dataset, url="https://example/x", parser="nwps", calc_expression_id=1)
    assert gs._add_source_main(ns) == 2


def test_hours_without_url_is_usage_error(dataset: Path) -> None:
    assert gs._add_source_main(_ns_add(dataset, calc_expression_id=1, hours="6,12")) == 2


def test_cli_happy_path_returns_zero(dataset: Path) -> None:
    ns = _ns_add(dataset, name="CLIW1", url="https://example/cli", parser="nwps")
    assert gs._add_source_main(ns) == 0
    assert any(r["name"] == "CLIW1" for r in _rows(dataset, "source"))


def test_not_a_directory_is_usage_error(tmp_path: Path) -> None:
    assert gs._add_source_main(_ns_add(tmp_path / "nope")) == 2


# --- reverse_engineer serializer refactor: byte-identical output --------------


def test_reverse_engineer_output_unchanged(dataset: Path) -> None:
    # The committed fixture sources.yaml was produced by reverse_engineer; the
    # serializer refactor (incl. always-emit-agency) must reproduce it byte-for-byte.
    committed = (dataset / "sources.yaml").read_bytes()
    gs.reverse_engineer(dataset)
    assert (dataset / "sources.yaml").read_bytes() == committed
