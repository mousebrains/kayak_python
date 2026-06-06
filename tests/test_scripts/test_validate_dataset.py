"""Tests for ``levels validate-dataset`` against the committed fixture dataset.

The fixture (``tests/fixtures/dataset``) is the redistribution-safe dataset the
engine's own CI validates without any kayak_data checkout (S4a of the
dataset-separation plan). These tests prove the validator passes the fixture
and catches each class of break it is responsible for.
"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest

from kayak.cli.validate_dataset import validate_dataset

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "dataset"


def test_fixture_dataset_is_valid() -> None:
    assert validate_dataset(FIXTURE) == []


@pytest.fixture
def dataset_copy(tmp_path: Path) -> Path:
    dst = tmp_path / "dataset"
    shutil.copytree(FIXTURE, dst)
    return dst


def _rewrite_csv(path: Path, mutate) -> None:
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    header = rows[0].keys() if rows else []
    mutate(rows)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(header))
        w.writeheader()
        w.writerows(rows)


def test_missing_required_file(dataset_copy: Path) -> None:
    (dataset_copy / "id_counters.csv").unlink()
    errs = validate_dataset(dataset_copy)
    assert any("id_counters.csv" in e for e in errs)


def test_duplicate_reach_id(dataset_copy: Path) -> None:
    _rewrite_csv(
        dataset_copy / "reach.csv",
        lambda rows: rows.__setitem__(1, {**rows[1], "id": rows[0]["id"]}),
    )
    errs = validate_dataset(dataset_copy)
    assert any("duplicate ids" in e for e in errs)


def test_stale_id_counter(dataset_copy: Path) -> None:
    _rewrite_csv(
        dataset_copy / "id_counters.csv",
        lambda rows: [r.update(next_id="1") for r in rows if r["table"] == "reach"],
    )
    errs = validate_dataset(dataset_copy)
    assert any("next_id" in e and "reach" in e for e in errs)


def test_duplicate_reach_name(dataset_copy: Path) -> None:
    _rewrite_csv(
        dataset_copy / "reach.csv",
        lambda rows: rows[1].update(name=rows[0]["name"]),
    )
    errs = validate_dataset(dataset_copy)
    assert any("duplicate reach.name" in e for e in errs)


def test_empty_reach_name(dataset_copy: Path) -> None:
    _rewrite_csv(dataset_copy / "reach.csv", lambda rows: rows[0].update(name=""))
    errs = validate_dataset(dataset_copy)
    assert any("empty name" in e for e in errs)


def test_reach_missing_geometry(dataset_copy: Path) -> None:
    geom_path = dataset_copy / "reaches.json"
    geom = json.loads(geom_path.read_text())
    geom.pop(next(iter(geom)))  # drop one reach's geometry
    geom_path.write_text(json.dumps(geom))
    errs = validate_dataset(dataset_copy)
    assert any("no geometry" in e for e in errs)


def test_orphan_geometry_key(dataset_copy: Path) -> None:
    geom_path = dataset_copy / "reaches.json"
    geom = json.loads(geom_path.read_text())
    geom["9999"] = next(iter(geom.values()))  # geom for a reach that does not exist
    geom_path.write_text(json.dumps(geom))
    errs = validate_dataset(dataset_copy)
    assert any("not in reach.csv" in e for e in errs)


def test_orphan_child_reach_id(dataset_copy: Path) -> None:
    _rewrite_csv(
        dataset_copy / "reach_state.csv",
        lambda rows: rows.append({"reach_id": "9999", "state_id": "1"}),
    )
    errs = validate_dataset(dataset_copy)
    assert any("reach_state.csv" in e and "not in reach.csv" in e for e in errs)


def test_broken_geometry_caught_by_check_reaches(dataset_copy: Path) -> None:
    # A LINESTRING() wrapper is exactly what check-reaches rejects.
    geom_path = dataset_copy / "reaches.json"
    geom = json.loads(geom_path.read_text())
    k = next(iter(geom))
    geom[k] = f"LINESTRING({geom[k]})"
    geom_path.write_text(json.dumps(geom))
    errs = validate_dataset(dataset_copy)
    assert any("check-reaches" in e for e in errs)
