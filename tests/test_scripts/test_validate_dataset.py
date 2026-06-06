"""Tests for ``levels validate-dataset`` against the committed fixture dataset.

The fixture (``tests/fixtures/dataset``) is the redistribution-safe dataset the
engine's own CI validates without any kayak_data checkout (S4a of the
dataset-separation plan). These tests prove the validator passes the fixture
and catches each class of break it is responsible for.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from kayak.cli.validate_dataset import validate_dataset

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "dataset"
BUILDER = Path(__file__).resolve().parents[1] / "fixtures" / "build_dataset_fixture.py"


def _load_builder():
    """Import the fixture generator to reuse its exact hashing helpers."""
    spec = importlib.util.spec_from_file_location("build_dataset_fixture", BUILDER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


# --- regression tests for the PR #123 round-2 review findings ----------------


def test_non_numeric_value_is_flagged(dataset_copy: Path) -> None:
    # Finding 1: SQLite would store "abc" in a REAL column silently, so the
    # materialized load can't catch it — the value check must.
    _rewrite_csv(dataset_copy / "gauge.csv", lambda rows: rows[0].update(latitude="abc"))
    errs = validate_dataset(dataset_copy)
    assert any(
        "gauge.csv" in e and "latitude" in e and ("decimal" in e or "number" in e) for e in errs
    )


def test_bad_enum_value_is_flagged(dataset_copy: Path) -> None:
    # Finding 1: an out-of-set enum value is a contract violation.
    _rewrite_csv(
        dataset_copy / "calc_expression.csv",
        lambda rows: rows[0].update(data_type="bogus"),
    )
    errs = validate_dataset(dataset_copy)
    assert any("calc_expression.csv" in e and "data_type" in e for e in errs)


def test_empty_value_in_not_null_column_is_flagged(dataset_copy: Path) -> None:
    # Finding 1: nullability is part of the per-column contract.
    _rewrite_csv(dataset_copy / "gauge.csv", lambda rows: rows[0].update(name=""))
    errs = validate_dataset(dataset_copy)
    assert any("gauge.csv" in e and "name" in e and "NOT NULL" in e for e in errs)


def test_missing_class_description_is_required(dataset_copy: Path) -> None:
    # Finding 2: a dataset is a complete projection — every contract CSV present.
    (dataset_copy / "class_description.csv").unlink()
    errs = validate_dataset(dataset_copy)
    assert any("class_description.csv" in e for e in errs)


def test_missing_gradient_json_is_required(dataset_copy: Path) -> None:
    # Finding 2: both JSON sidecars are required (empty is "{}", not absent).
    (dataset_copy / "reaches-gradient.json").unlink()
    errs = validate_dataset(dataset_copy)
    assert any("reaches-gradient.json" in e for e in errs)


def test_missing_reach_class_is_required(dataset_copy: Path) -> None:
    # Finding 2 (reviewer's repro): deleting reach_class.csv + its counter must
    # not validate.
    (dataset_copy / "reach_class.csv").unlink()
    _rewrite_csv(
        dataset_copy / "id_counters.csv",
        lambda rows: rows.__setitem__(
            slice(None), [r for r in rows if r["table"] != "reach_class"]
        ),
    )
    errs = validate_dataset(dataset_copy)
    assert any("reach_class.csv" in e for e in errs)


def test_duplicate_header_is_flagged(dataset_copy: Path) -> None:
    # Finding 3 (reviewer's repro): header `id,name,abbreviation,id` collapses to
    # the expected set, so the set check passed it; duplicate names must be
    # rejected before the set comparison.
    p = dataset_copy / "state.csv"
    lines = p.read_text().splitlines()
    out = [lines[0] + ",id"]
    out += [ln + "," + ln.split(",")[0] for ln in lines[1:]]
    p.write_text("\n".join(out) + "\n")
    errs = validate_dataset(dataset_copy)
    assert any("state.csv" in e and "duplicate column" in e for e in errs)


def test_short_row_is_flagged(dataset_copy: Path) -> None:
    # Finding 3: a row with the wrong number of fields is a structural error.
    p = dataset_copy / "gauge_source.csv"
    p.write_text(p.read_text() + "1\n")  # one field, header has two
    errs = validate_dataset(dataset_copy)
    assert any("gauge_source.csv" in e and "fields" in e for e in errs)


# --- regression tests for the PR #123 round-3 review findings ----------------


def test_bad_datetime_does_not_crash_materialize(dataset_copy: Path) -> None:
    # Finding 1: a non-ISO datetime would load fine (SQLite text) then crash
    # SQLAlchemy's decoder mid-scan. The value error must gate materialization.
    _rewrite_csv(dataset_copy / "reach.csv", lambda rows: rows[0].update(updated_at="not-a-date"))
    errs = validate_dataset(dataset_copy)  # must not raise
    assert any("updated_at" in e and "ISO datetime" in e for e in errs)


def test_duplicate_composite_pk_is_flagged(dataset_copy: Path) -> None:
    # Finding 2: SQLite upsert collapses a duplicate composite-PK row.
    _rewrite_csv(
        dataset_copy / "gauge_source.csv",
        lambda rows: rows.append({"gauge_id": "1", "source_id": "1"}),
    )
    errs = validate_dataset(dataset_copy)
    assert any("gauge_source.csv" in e and "duplicate primary key" in e for e in errs)


def test_duplicate_natural_pk_is_flagged(dataset_copy: Path) -> None:
    # Finding 2: class_description's PK is its name.
    _rewrite_csv(
        dataset_copy / "class_description.csv",
        lambda rows: rows.append({"name": "II", "description": "duplicate"}),
    )
    errs = validate_dataset(dataset_copy)
    assert any("class_description.csv" in e and "duplicate primary key" in e for e in errs)


def test_duplicate_json_key_is_flagged(dataset_copy: Path) -> None:
    # Finding 2: json.loads keeps the last of duplicate members, dropping geometry.
    geom = json.loads((dataset_copy / "reaches.json").read_text())
    raw = '{{"1": {}, "1": {}, "2": {}, "3": {}}}'.format(
        json.dumps(geom["1"]), json.dumps(geom["1"]), json.dumps(geom["2"]), json.dumps(geom["3"])
    )
    (dataset_copy / "reaches.json").write_text(raw)
    errs = validate_dataset(dataset_copy)
    assert any("reaches.json" in e and "duplicate keys" in e for e in errs)


def test_noncanonical_json_key_is_flagged(dataset_copy: Path) -> None:
    # Finding 2: "1" and "01" normalize to the same reach id.
    geom = json.loads((dataset_copy / "reaches.json").read_text())
    raw = '{{"1": {}, "01": {}, "2": {}, "3": {}}}'.format(
        json.dumps(geom["1"]), json.dumps(geom["1"]), json.dumps(geom["2"]), json.dumps(geom["3"])
    )
    (dataset_copy / "reaches.json").write_text(raw)
    errs = validate_dataset(dataset_copy)
    assert any("reaches.json" in e and "non-canonical" in e for e in errs)


def test_string_too_long_is_flagged(dataset_copy: Path) -> None:
    # Finding 3: state.abbreviation is String(2).
    _rewrite_csv(dataset_copy / "state.csv", lambda rows: rows[0].update(abbreviation="ORE"))
    errs = validate_dataset(dataset_copy)
    assert any("state.csv" in e and "abbreviation" in e and "max length" in e for e in errs)


def test_coordinate_out_of_range_is_flagged(dataset_copy: Path) -> None:
    # Finding 3: an absurd coordinate (1e300) is caught.
    _rewrite_csv(dataset_copy / "gauge.csv", lambda rows: rows[0].update(latitude="1e300"))
    errs = validate_dataset(dataset_copy)
    assert any("gauge.csv" in e and "latitude" in e for e in errs)


def test_coordinate_scale_is_enforced(dataset_copy: Path) -> None:
    # Finding 3: latitude is Numeric(9,6) — 7 decimal places violates the scale.
    _rewrite_csv(dataset_copy / "gauge.csv", lambda rows: rows[0].update(latitude="44.1234567"))
    errs = validate_dataset(dataset_copy)
    assert any("latitude" in e and "scale" in e for e in errs)


def test_non_positive_id_is_flagged(dataset_copy: Path) -> None:
    # Finding 3: a stable id must be a positive canonical integer.
    _rewrite_csv(dataset_copy / "state.csv", lambda rows: rows[0].update(id="0"))
    errs = validate_dataset(dataset_copy)
    assert any("state.csv" in e and "positive integer id" in e for e in errs)


def test_non_positive_fk_is_flagged(dataset_copy: Path) -> None:
    # Finding 3: an FK to an id is held to the same positive-canonical rule.
    _rewrite_csv(dataset_copy / "reach.csv", lambda rows: rows[0].update(gauge_id="0"))
    errs = validate_dataset(dataset_copy)
    assert any("reach.csv" in e and "gauge_id" in e and "positive integer id" in e for e in errs)


def test_id_counters_wrong_width_is_flagged(dataset_copy: Path) -> None:
    # Finding 4: id_counters.csv rows get the same width/value pass.
    p = dataset_copy / "id_counters.csv"
    p.write_text(p.read_text().replace("state,3\n", "state,3,extra\n"))
    errs = validate_dataset(dataset_copy)
    assert any("id_counters.csv" in e and "fields" in e for e in errs)


def test_id_counters_blank_cell_is_flagged(dataset_copy: Path) -> None:
    # Finding 4: a blank table cell is no longer silently ignored.
    p = dataset_copy / "id_counters.csv"
    p.write_text(p.read_text() + ",5\n")
    errs = validate_dataset(dataset_copy)
    assert any("id_counters.csv" in e and "NOT NULL" in e for e in errs)


def test_provenance_matches_committed_fixture() -> None:
    # Finding 4: the committed fixture's geometry/facts/gradient must match the
    # recorded provenance digests, so a hand-edit (or a regen from a dirty
    # source) that drifts from the manifest is caught in CI without needing the
    # source checkout.
    b = _load_builder()
    prov = json.loads((FIXTURE / "PROVENANCE.json").read_text())
    rows = {r["id"]: r for r in csv.DictReader((FIXTURE / "reach.csv").open(encoding="utf-8"))}
    geom = json.loads((FIXTURE / "reaches.json").read_text())
    grad = json.loads((FIXTURE / "reaches-gradient.json").read_text())
    assert prov["reaches"]
    for pr in prov["reaches"]:
        fid = str(pr["fixture_reach_id"])
        assert b._sha256(geom[fid]) == pr["geom_sha256"]
        facts = "|".join(f"{c}={rows[fid][c]}" for c in b.COPIED_REACH_COLS)
        assert b._sha256(facts) == pr["facts_sha256"]
        if fid in grad:
            assert b._sha256(b._grad_str(grad[fid])) == pr["gradient_sha256"]
        else:
            assert pr["gradient_sha256"] == ""
