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


# --- regression tests for the PR #123 review findings -----------------------


def test_extreme_gradient_peak_caught(dataset_copy: Path) -> None:
    # Finding 1: the gradient profile must be applied before check-reaches, so
    # an extreme peak (>1000 ft/mi) is caught. Previously gradient_profile was
    # never loaded into the temp DB, so this passed silently.
    grad_path = dataset_copy / "reaches-gradient.json"
    grad = json.loads(grad_path.read_text())
    k = next(iter(grad))
    profile = json.loads(grad[k])
    profile["samples"][0]["grad_ft_per_mi"] = 5000.0
    grad[k] = json.dumps(profile)
    grad_path.write_text(json.dumps(grad))
    errs = validate_dataset(dataset_copy)
    assert any("check-reaches" in e for e in errs)


def test_missing_reach_state_is_required(dataset_copy: Path) -> None:
    # Finding 2: reach_state is part of the required-file set (was optional).
    (dataset_copy / "reach_state.csv").unlink()
    errs = validate_dataset(dataset_copy)
    assert any("reach_state.csv" in e for e in errs)


def test_removed_counter_is_caught(dataset_copy: Path) -> None:
    # Finding 3: counter coverage is driven by the known id-bearing tables, not
    # the submitted list — dropping a counter cannot disable that table's checks.
    _rewrite_csv(
        dataset_copy / "id_counters.csv",
        lambda rows: rows.__setitem__(slice(None), [r for r in rows if r["table"] != "reach"]),
    )
    errs = validate_dataset(dataset_copy)
    assert any("missing counter for id-bearing table reach" in e for e in errs)


def test_dup_id_via_removed_counter_is_caught(dataset_copy: Path) -> None:
    # Finding 3 (reviewer's exact repro): remove the state counter and collapse
    # two states to one id. The missing-counter check fires regardless.
    _rewrite_csv(
        dataset_copy / "id_counters.csv",
        lambda rows: rows.__setitem__(slice(None), [r for r in rows if r["table"] != "state"]),
    )
    _rewrite_csv(dataset_copy / "state.csv", lambda rows: rows[1].update(id="1"))
    errs = validate_dataset(dataset_copy)
    assert any("counter for id-bearing table state" in e for e in errs)


def test_unexpected_csv_is_flagged(dataset_copy: Path) -> None:
    (dataset_copy / "bogus.csv").write_text("id,x\n1,2\n")
    errs = validate_dataset(dataset_copy)
    assert any("unexpected CSV" in e and "bogus.csv" in e for e in errs)


def test_column_mismatch_is_flagged(dataset_copy: Path) -> None:
    # An extra/renamed column is a contract violation, not a silent pass.
    src = (dataset_copy / "state.csv").read_text().splitlines()
    src[0] = src[0] + ",surprise"
    (dataset_copy / "state.csv").write_text(
        "\n".join(c + ",x" if i else c for i, c in enumerate(src)) + "\n"
    )
    errs = validate_dataset(dataset_copy)
    assert any("state.csv" in e and "column mismatch" in e for e in errs)


def test_malformed_id_counter_header_does_not_crash(dataset_copy: Path) -> None:
    # Finding 4: a renamed next_id column must yield a focused error, not a
    # KeyError traceback.
    p = dataset_copy / "id_counters.csv"
    p.write_text(p.read_text().replace("next_id", "nxt"))
    errs = validate_dataset(dataset_copy)  # must not raise
    assert errs and any("id_counters" in e for e in errs)


def test_non_integer_id_does_not_crash(dataset_copy: Path) -> None:
    # Finding 4: a non-integer id is reported, not a ValueError traceback.
    _rewrite_csv(dataset_copy / "reach.csv", lambda rows: rows[0].update(id="abc"))
    errs = validate_dataset(dataset_copy)  # must not raise
    assert errs and any("non-integer" in e for e in errs)


def test_malformed_geometry_json_does_not_crash(dataset_copy: Path) -> None:
    (dataset_copy / "reaches.json").write_text("{ not valid json")
    errs = validate_dataset(dataset_copy)  # must not raise
    assert errs
