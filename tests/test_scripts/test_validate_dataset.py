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
import struct
import zlib
from pathlib import Path

import pytest

from kayak.cli.validate_dataset import (
    _regression_closure,
    _strip_code_fences,
    validate_dataset,
)

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


def _add_column(path: Path, col: str, value: str) -> None:
    """Append a new column ``col`` (set to ``value`` on every row) to a CSV."""
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    fieldnames = (list(rows[0].keys()) if rows else []) + [col]
    for r in rows:
        r[col] = value
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def _write_required_site_prose(dataset: Path) -> None:
    site = dataset / "site"
    site.mkdir(exist_ok=True)
    for page in ("privacy", "disclaimer", "contact"):
        (site / f"{page}.md").write_text(f"## {page.title()}\n\nText.\n", encoding="utf-8")


def _set_dataset_status(dataset: Path, status: str) -> None:
    manifest = dataset / "dataset.yaml"
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(
        text.replace("status: publishable\n", f"status: {status}\n"), encoding="utf-8"
    )


def _png_bytes(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return (
            struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _ico_bytes(width: int, height: int) -> bytes:
    png = _png_bytes(width, height)
    directory = struct.pack("<BBBBHHII", width, height, 0, 0, 1, 32, len(png), 22)
    return struct.pack("<HHH", 0, 1, 1) + directory + png


def test_optional_unknown_station_policy_column_accepted(dataset_copy: Path) -> None:
    """The fetch_url.unknown_station_policy column (S1) may be PRESENT with a value
    and the dataset still fully validates (text/nullable value check passes). The
    complementary ABSENCE case — the column omitted, which is what makes the
    introduction backward-compatible — is covered by test_fixture_dataset_is_valid
    (the fixture's fetch_url.csv carries no such column)."""
    _add_column(dataset_copy / "fetch_url.csv", "unknown_station_policy", "ignore")
    assert validate_dataset(dataset_copy) == []


def test_map_yaml_is_validated(dataset_copy: Path) -> None:
    (dataset_copy / "map.yaml").write_text("center: [200.0, 0.0]\n")
    errs = validate_dataset(dataset_copy)
    assert any("map.yaml" in e and "center" in e for e in errs)


def test_map_yaml_reserved_layer_key_rejected(dataset_copy: Path) -> None:
    (dataset_copy / "map.yaml").write_text(
        "layers:\n"
        "  - key: s\n"
        "    label: Bad Layer\n"
        "    color: '#abcdef'\n"
        "    shape: circle\n"
        "    size: 5\n"
        "    popup: access\n"
        "    popup_link: https://example.com/layer\n"
        "    output_filename: bad-layer.geojson\n"
        "    endpoint: https://services.example.com/FeatureServer/0\n"
        "    out_fields: [name]\n"
    )
    errs = validate_dataset(dataset_copy)
    assert any("map.yaml" in e and "reserved" in e for e in errs)


def test_unexpected_extra_column_still_rejected(dataset_copy: Path) -> None:
    """Allowing OPTIONAL columns to be absent must NOT let an arbitrary unknown
    column through — a non-contract column is still a shape error."""
    _add_column(dataset_copy / "fetch_url.csv", "bogus_col", "x")
    errs = validate_dataset(dataset_copy)
    assert any("bogus_col" in e and "column mismatch" in e for e in errs)


def test_bad_unknown_station_policy_value_rejected(dataset_copy: Path) -> None:
    """A non-canonical unknown_station_policy (S1) is caught at the dataset level,
    not silently demoted to reject at fetch time."""
    _add_column(dataset_copy / "fetch_url.csv", "unknown_station_policy", "ingore")
    errs = validate_dataset(dataset_copy)
    assert any("unknown_station_policy must be blank or one of" in e for e in errs)


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


def test_orphan_source_rejected(dataset_copy: Path) -> None:
    # Every source must have a gauge: drop source 1's gauge_source row -> orphan.
    def _drop_source_1(rows: list[dict]) -> None:
        rows[:] = [r for r in rows if r["source_id"] != "1"]

    _rewrite_csv(dataset_copy / "gauge_source.csv", _drop_source_1)
    errs = validate_dataset(dataset_copy)
    assert any("no gauge_source row" in e and "[1]" in e for e in errs)


def test_double_linked_source_rejected(dataset_copy: Path) -> None:
    # A source linked to two gauges (source->gauge must be 1-to-1).
    _rewrite_csv(
        dataset_copy / "gauge_source.csv",
        lambda rows: rows.append({"gauge_id": "2", "source_id": "1"}),
    )
    errs = validate_dataset(dataset_copy)
    assert any("more than one gauge" in e and "[1]" in e for e in errs)


def test_gauge_source_dangling_source_id_rejected(dataset_copy: Path) -> None:
    _rewrite_csv(
        dataset_copy / "gauge_source.csv",
        lambda rows: rows.append({"gauge_id": "1", "source_id": "999"}),
    )
    errs = validate_dataset(dataset_copy)
    assert any("references source ids not in source.csv" in e and "999" in e for e in errs)


def test_gauge_source_dangling_gauge_id_rejected(dataset_copy: Path) -> None:
    # Repoint source 1's link at a non-existent gauge (stays exactly-one, bad FK).
    def _mutate(rows: list[dict]) -> None:
        for r in rows:
            if r["source_id"] == "1":
                r["gauge_id"] = "999"

    _rewrite_csv(dataset_copy / "gauge_source.csv", _mutate)
    errs = validate_dataset(dataset_copy)
    assert any("references gauge ids not in gauge.csv" in e and "999" in e for e in errs)


def test_reach_dangling_gauge_id_rejected(dataset_copy: Path) -> None:
    def _mutate(rows: list[dict]) -> None:
        rows[0]["gauge_id"] = "999"

    _rewrite_csv(dataset_copy / "reach.csv", _mutate)
    errs = validate_dataset(dataset_copy)
    assert any("reach.csv references gauge ids not in gauge.csv" in e and "999" in e for e in errs)


def test_same_gauge_twice_is_only_a_duplicate_pk(dataset_copy: Path) -> None:
    # An exact-duplicate gauge_source row is a duplicate-PK error, NOT "more than
    # one gauge" — _check_gauge_source counts distinct gauges, so it stays silent.
    _rewrite_csv(
        dataset_copy / "gauge_source.csv",
        lambda rows: rows.append({"gauge_id": "1", "source_id": "1"}),
    )
    errs = validate_dataset(dataset_copy)
    assert any("duplicate primary key" in e for e in errs)
    assert not any("more than one gauge" in e for e in errs)


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


# --- regression tests for the PR #123 round-4 review findings ----------------


def test_null_geometry_value_is_flagged(dataset_copy: Path) -> None:
    # Finding 1: a present key with a null value is not "has geometry".
    geom = json.loads((dataset_copy / "reaches.json").read_text())
    geom["1"] = None
    (dataset_copy / "reaches.json").write_text(json.dumps(geom))
    errs = validate_dataset(dataset_copy)
    assert any("reaches.json" in e and "non-string" in e for e in errs)


def test_empty_geometry_value_is_flagged(dataset_copy: Path) -> None:
    # Finding 1: an empty-string geometry materializes as empty (treated optional).
    geom = json.loads((dataset_copy / "reaches.json").read_text())
    geom["1"] = ""
    (dataset_copy / "reaches.json").write_text(json.dumps(geom))
    errs = validate_dataset(dataset_copy)
    assert any("reaches.json" in e and "1" in e for e in errs)


def test_nan_geometry_value_is_flagged(dataset_copy: Path) -> None:
    # Finding 1: json.loads accepts NaN by default; it must be rejected.
    geom = json.loads((dataset_copy / "reaches.json").read_text())
    geom["1"] = float("nan")
    (dataset_copy / "reaches.json").write_text(json.dumps(geom))  # writes bare NaN
    errs = validate_dataset(dataset_copy)
    assert any("reaches.json" in e for e in errs)


def test_null_gradient_value_is_flagged(dataset_copy: Path) -> None:
    # Finding 1: gradient entries get the same present-value validation.
    grad = json.loads((dataset_copy / "reaches-gradient.json").read_text())
    grad[next(iter(grad))] = None
    (dataset_copy / "reaches-gradient.json").write_text(json.dumps(grad))
    errs = validate_dataset(dataset_copy)
    assert any("reaches-gradient.json" in e and "non-string" in e for e in errs)


def test_lone_noncanonical_json_key_is_flagged(dataset_copy: Path) -> None:
    # Finding 2: a single non-canonical key (no collision) must still be rejected.
    geom = json.loads((dataset_copy / "reaches.json").read_text())
    geom["01"] = geom.pop("1")
    (dataset_copy / "reaches.json").write_text(json.dumps(geom))
    errs = validate_dataset(dataset_copy)
    assert any("reaches.json" in e and "non-canonical" in e for e in errs)


def test_duplicate_numeric_composite_pk_is_flagged(dataset_copy: Path) -> None:
    # Finding 3: (1, 1) and (1, 1.0) are the same REAL PK in SQLite.
    (dataset_copy / "rating.csv").write_text("id,url,parser\n1,,\n")
    _rewrite_csv(
        dataset_copy / "id_counters.csv",
        lambda rows: [r.update(next_id="2") for r in rows if r["table"] == "rating"],
    )
    (dataset_copy / "rating_data.csv").write_text(
        "rating_id,gauge_height_ft,flow_cfs\n1,1,10\n1,1.0,20\n"
    )
    errs = validate_dataset(dataset_copy)
    assert any("rating_data.csv" in e and "duplicate primary key" in e for e in errs)


def test_underscore_integer_is_flagged(dataset_copy: Path) -> None:
    # Finding 4: Python int() accepts "1_0"; SQLite stores it as TEXT.
    _rewrite_csv(dataset_copy / "reach.csv", lambda rows: rows[0].update(aw_id="1_0"))
    errs = validate_dataset(dataset_copy)
    assert any("reach.csv" in e and "aw_id" in e and "integer" in e for e in errs)


def test_underscore_float_is_flagged(dataset_copy: Path) -> None:
    _rewrite_csv(dataset_copy / "gauge.csv", lambda rows: rows[0].update(bank_full="1_0"))
    errs = validate_dataset(dataset_copy)
    assert any("gauge.csv" in e and "bank_full" in e for e in errs)


def test_underscore_decimal_is_flagged(dataset_copy: Path) -> None:
    _rewrite_csv(dataset_copy / "gauge.csv", lambda rows: rows[0].update(latitude="4_4.0"))
    errs = validate_dataset(dataset_copy)
    assert any("gauge.csv" in e and "latitude" in e for e in errs)


def test_integer_out_of_64bit_range_is_flagged(dataset_copy: Path) -> None:
    # Finding 4: 2**63 overflows SQLite's signed-64-bit INTEGER -> REAL affinity.
    _rewrite_csv(
        dataset_copy / "reach.csv", lambda rows: rows[0].update(aw_id="9223372036854775808")
    )
    errs = validate_dataset(dataset_copy)
    assert any("reach.csv" in e and "aw_id" in e and "range" in e for e in errs)


def test_invalid_utf8_csv_does_not_crash(dataset_copy: Path) -> None:
    # Finding 5: an invalid UTF-8 byte must yield a focused error, not a traceback.
    (dataset_copy / "state.csv").write_bytes(b"id,name,abbreviation\n1,\xff,OR\n")
    errs = validate_dataset(dataset_copy)  # must not raise
    assert any("state.csv" in e and "UTF-8" in e for e in errs)


def test_invalid_utf8_id_counters_does_not_crash(dataset_copy: Path) -> None:
    (dataset_copy / "id_counters.csv").write_bytes(b"table,next_id\n\xff,3\n")
    errs = validate_dataset(dataset_copy)  # must not raise
    assert any("id_counters.csv" in e and "UTF-8" in e for e in errs)


# --- regression tests for the PR #123 round-5 review findings ----------------


@pytest.mark.parametrize("bad", ["null", "[]", "1", '{"samples": "bad"}', '{"samples": {}}', "NaN"])
def test_wrong_shaped_gradient_profile_is_flagged(dataset_copy: Path, bad: str) -> None:
    # Finding 1: the JSON *inside* the gradient string must meet the
    # object/samples-list contract — valid JSON of the wrong shape, not only
    # invalid syntax, must fail.
    grad = json.loads((dataset_copy / "reaches-gradient.json").read_text())
    k = next(iter(grad))
    grad[k] = bad
    (dataset_copy / "reaches-gradient.json").write_text(json.dumps(grad))
    errs = validate_dataset(dataset_copy)
    assert any("reaches-gradient.json" in e and "reach" in e for e in errs)


def test_gradient_sample_missing_field_is_flagged(dataset_copy: Path) -> None:
    # Finding 1: each sample needs the numeric plotting fields.
    grad = json.loads((dataset_copy / "reaches-gradient.json").read_text())
    k = next(iter(grad))
    prof = json.loads(grad[k])
    del prof["samples"][0]["grad_ft_per_mi"]
    grad[k] = json.dumps(prof)
    (dataset_copy / "reaches-gradient.json").write_text(json.dumps(grad))
    errs = validate_dataset(dataset_copy)
    assert any("reaches-gradient.json" in e and "grad_ft_per_mi" in e for e in errs)


def test_long_integer_csv_id_does_not_crash(dataset_copy: Path) -> None:
    # Finding 2: a 5,000-digit id must not raise Python's str->int limit.
    _rewrite_csv(dataset_copy / "state.csv", lambda rows: rows[0].update(id="9" * 5000))
    errs = validate_dataset(dataset_copy)  # must not raise
    assert errs and any("state.csv" in e and "range" in e for e in errs)


def test_long_integer_non_id_does_not_crash(dataset_copy: Path) -> None:
    _rewrite_csv(dataset_copy / "reach.csv", lambda rows: rows[0].update(aw_id="9" * 5000))
    errs = validate_dataset(dataset_copy)  # must not raise
    assert errs and any("aw_id" in e and "range" in e for e in errs)


def test_long_integer_json_key_does_not_crash(dataset_copy: Path) -> None:
    geom = json.loads((dataset_copy / "reaches.json").read_text())
    geom["9" * 5000] = geom.pop("1")
    (dataset_copy / "reaches.json").write_text(json.dumps(geom))
    errs = validate_dataset(dataset_copy)  # must not raise
    assert errs and any("reaches.json" in e and "range" in e for e in errs)


def test_compact_datetime_is_flagged(dataset_copy: Path) -> None:
    # Finding 3: fromisoformat accepts "20240101" but SQLite stores it as INTEGER.
    _rewrite_csv(dataset_copy / "reach.csv", lambda rows: rows[0].update(updated_at="20240101"))
    errs = validate_dataset(dataset_copy)
    assert any("reach.csv" in e and "updated_at" in e and "datetime" in e for e in errs)


# --- regression tests for the PR #123 round-6 review findings ----------------


def _set_gradient(dataset_copy: Path, inner: str) -> list[str]:
    grad = json.loads((dataset_copy / "reaches-gradient.json").read_text())
    grad[next(iter(grad))] = inner  # the sidecar value is a JSON-encoded string
    (dataset_copy / "reaches-gradient.json").write_text(json.dumps(grad))
    return validate_dataset(dataset_copy)


_OK = '{"d_mi":1.5,"w_mi":1,"grad_ft_per_mi":11}'


@pytest.mark.parametrize(
    "inner,expect",
    [
        # Finding 1: standard JSON number 1e999 decodes to inf (not via parse_constant).
        (f'{{"samples":[{{"d_mi":0.5,"w_mi":1e999,"grad_ft_per_mi":10}},{_OK}]}}', "finite number"),
        # Finding 1: optional field types.
        (
            f'{{"samples":[{{"d_mi":0.5,"w_mi":1,"grad_ft_per_mi":10,"significant":"false"}},{_OK}]}}',
            "boolean",
        ),
        (
            f'{{"samples":[{{"d_mi":0.5,"w_mi":1,"grad_ft_per_mi":10,"lat":[]}},{_OK}]}}',
            "finite number or null",
        ),
        # Finding 1: domain/order invariants.
        (f'{{"samples":[{{"d_mi":0.5,"w_mi":0,"grad_ft_per_mi":10}},{_OK}]}}', "positive"),
        (
            '{"samples":[{"d_mi":1.5,"w_mi":1,"grad_ft_per_mi":10},'
            '{"d_mi":0.5,"w_mi":1,"grad_ft_per_mi":11}]}',
            "ordered",
        ),
        # Finding 2: duplicate members at profile and sample depth.
        ('{"samples":[{"d_mi":0.5,"w_mi":1,"grad_ft_per_mi":10}],"samples":[]}', "duplicate key"),
        (
            f'{{"samples":[{{"d_mi":0.5,"w_mi":1,"grad_ft_per_mi":10,"d_mi":0.7}},{_OK}]}}',
            "duplicate key",
        ),
    ],
)
def test_gradient_sample_domain_and_dupes(dataset_copy: Path, inner: str, expect: str) -> None:
    errs = _set_gradient(dataset_copy, inner)
    assert any("reaches-gradient.json" in e and expect in e for e in errs)


def test_leading_zero_counter_is_not_false_overflow(dataset_copy: Path) -> None:
    # Non-blocking: a numerically valid leading-zero next_id (= 3) must not be
    # misreported as a 64-bit overflow.
    _rewrite_csv(
        dataset_copy / "id_counters.csv",
        lambda rows: [r.update(next_id="0" * 20 + "3") for r in rows if r["table"] == "state"],
    )
    assert validate_dataset(dataset_copy) == []


# --- regression tests for the PR #123 round-7 review findings ----------------


def test_huge_integer_gradient_does_not_crash(dataset_copy: Path) -> None:
    # P1: a 4,000-digit integer parses, but math.isfinite(float(int)) overflows.
    inner = '{"samples":[{"d_mi":0.5,"w_mi":1,"grad_ft_per_mi":' + "9" * 4000 + "}," + _OK + "]}"
    errs = _set_gradient(dataset_copy, inner)  # must not raise
    assert any("reaches-gradient.json" in e and "finite number" in e for e in errs)


@pytest.mark.parametrize(
    "inner,expect",
    [
        ('{"samples":[{"d_mi":-0.5,"w_mi":1,"grad_ft_per_mi":10},' + _OK + "]}", "non-negative"),
        ('{"samples":[{"d_mi":0.5,"w_mi":1,"grad_ft_per_mi":-10},' + _OK + "]}", "non-negative"),
    ],
)
def test_gradient_out_of_domain_is_flagged(dataset_copy: Path, inner: str, expect: str) -> None:
    errs = _set_gradient(dataset_copy, inner)
    assert any("reaches-gradient.json" in e and expect in e for e in errs)


def test_gradient_is_decoupled_from_reach_length(dataset_copy: Path) -> None:
    # Round 8: gradient extent and reach.length legitimately diverge (reservoirs
    # stop short; traces overshoot), so the validator no longer rejects a sample
    # past reach.length (fixture reach 1 is 7.7 mi). The renderer owns the
    # x-domain (clips overshoot, shows reservoir gaps as zero gradient).
    inner = '{"samples":[' + _OK + ',{"d_mi":100,"w_mi":1,"grad_ft_per_mi":10}]}'
    errs = _set_gradient(dataset_copy, inner)
    assert not any("reaches-gradient.json" in e for e in errs)


def test_gradient_leading_gap_is_allowed(dataset_copy: Path) -> None:
    # Symmetric to the trailing reservoir: a profile whose first window starts
    # mid-reach (a lake at the put-in) is valid gradient data — the renderer
    # shows the leading span as zero gradient, hover reads "no gradient data".
    # The validator stays integrity-only and does not enforce a put-in start.
    inner = (
        '{"samples":[{"d_mi":2,"w_mi":0.25,"grad_ft_per_mi":80},'
        '{"d_mi":2.5,"w_mi":0.25,"grad_ft_per_mi":40}]}'
    )
    errs = _set_gradient(dataset_copy, inner)
    assert not any("reaches-gradient.json" in e for e in errs)


def test_long_zero_padded_counter_does_not_crash(dataset_copy: Path) -> None:
    # P2: "0"*5000 + "3" = 3; must not hit Python's str->int digit limit.
    _rewrite_csv(
        dataset_copy / "id_counters.csv",
        lambda rows: [r.update(next_id="0" * 5000 + "3") for r in rows if r["table"] == "state"],
    )
    assert validate_dataset(dataset_copy) == []  # must not raise; value is 3


def test_excess_datetime_fraction_is_flagged(dataset_copy: Path) -> None:
    # P2: 7 fractional digits cannot round-trip through datetime (microseconds).
    _rewrite_csv(
        dataset_copy / "reach.csv",
        lambda rows: rows[0].update(updated_at="2024-01-01 00:00:00.1234567"),
    )
    errs = validate_dataset(dataset_copy)
    assert any("updated_at" in e and "datetime" in e for e in errs)


def test_microsecond_datetime_is_accepted(dataset_copy: Path) -> None:
    # P2: exactly 6 fractional digits is the round-trippable maximum.
    _rewrite_csv(
        dataset_copy / "reach.csv",
        lambda rows: rows[0].update(updated_at="2024-01-01 00:00:00.123456"),
    )
    assert not any("updated_at" in e for e in validate_dataset(dataset_copy))


# --- consolidated from the former METADATA_DIR-reading code tests ------------


def test_usgs_source_non_numeric_name_is_flagged(dataset_copy: Path) -> None:
    # The USGS-OGC fetch keys on source.name as the station id, so a USGS source
    # must be a bare numeric id. Consolidated from the deleted
    # test_fetch_usgs_ogc::test_usgs_source_names_are_station_ids so the data
    # repo's CI gates it via validate-dataset.
    _rewrite_csv(
        dataset_copy / "source.csv",
        lambda rows: [r.update(name="not-a-number") for r in rows if r.get("agency") == "USGS"],
    )
    errs = validate_dataset(dataset_copy)
    assert any("source.csv" in e and "USGS" in e and "numeric" in e for e in errs)


def test_usgs_source_non_ascii_digit_name_is_flagged(dataset_copy: Path) -> None:
    # str.isdigit() alone accepts non-ASCII digits (e.g. "١٤") the OGC fetch URL
    # can't use; the check requires ASCII digits. (PR #124 review nit.)
    _rewrite_csv(
        dataset_copy / "source.csv",
        lambda rows: [r.update(name="١٤") for r in rows if r.get("agency") == "USGS"],
    )
    errs = validate_dataset(dataset_copy)
    assert any("source.csv" in e and "USGS" in e for e in errs)


def test_loader_row_skip_makes_count_mismatch(dataset_copy: Path, monkeypatch) -> None:
    # Anti-vacuity guard: if the loader silently dropped a reach row, the
    # check-reaches scan would cover fewer reaches than reach.csv declares. Patch
    # the loader to drop one row and assert the count mismatch is reported.
    # (PR #124 review nit — restores test_committed_reach_geom's count guard.)
    import kayak.db.metadata_csv as mc

    real = mc.upsert_csvs

    def dropping(conn, in_dir):
        counts = real(conn, in_dir)
        conn.execute("DELETE FROM reach WHERE id = (SELECT MAX(id) FROM reach)")
        return counts

    monkeypatch.setattr(mc, "upsert_csvs", dropping)
    errs = validate_dataset(dataset_copy)
    assert any("materialized" in e and "reach.csv declares" in e for e in errs)


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


# --- dataset contract (dataset.yaml) — S6.2 -------------------------------


def test_missing_dataset_yaml_is_contract_zero(dataset_copy: Path) -> None:
    (dataset_copy / "dataset.yaml").unlink()
    errs = validate_dataset(dataset_copy)
    # Contract-0 rejection, gated first: it's the ONLY error (no content checks run).
    assert errs == [e for e in errs if "dataset.yaml" in e]
    assert any("contract 0" in e and "requires contract" in e for e in errs)


def test_dataset_yaml_malformed_yaml(dataset_copy: Path) -> None:
    (dataset_copy / "dataset.yaml").write_text("contract_version: 1\n  bad: : indent\n")
    assert any("invalid YAML" in e for e in validate_dataset(dataset_copy))


def test_dataset_yaml_non_mapping(dataset_copy: Path) -> None:
    (dataset_copy / "dataset.yaml").write_text("- just\n- a\n- list\n")
    assert any("must be a mapping" in e for e in validate_dataset(dataset_copy))


def test_dataset_yaml_contract_version_out_of_range(dataset_copy: Path) -> None:
    (dataset_copy / "dataset.yaml").write_text(
        "contract_version: 99\ndataset_id: x\nname: y\nstatus: publishable\n"
        'license: L\nengine_test_ref: "%s"\n' % ("0" * 40)
    )
    assert any("contract_version 99 is outside" in e for e in validate_dataset(dataset_copy))


def test_dataset_yaml_bad_status(dataset_copy: Path) -> None:
    (dataset_copy / "dataset.yaml").write_text(
        "contract_version: 1\ndataset_id: x\nname: y\nstatus: draft\n"
        'license: L\nengine_test_ref: "%s"\n' % ("0" * 40)
    )
    assert any("status must be one of" in e for e in validate_dataset(dataset_copy))


def test_dataset_yaml_bad_engine_test_ref(dataset_copy: Path) -> None:
    (dataset_copy / "dataset.yaml").write_text(
        "contract_version: 1\ndataset_id: x\nname: y\nstatus: publishable\n"
        "license: L\nengine_test_ref: not-a-sha\n"
    )
    assert any("engine_test_ref must be" in e for e in validate_dataset(dataset_copy))


def test_dataset_yaml_duplicate_key(dataset_copy: Path) -> None:
    # A duplicated contract_version must be rejected as malformed (not last-wins).
    (dataset_copy / "dataset.yaml").write_text(
        "contract_version: 99\ncontract_version: 1\ndataset_id: x\nname: y\n"
        'status: publishable\nlicense: L\nengine_test_ref: "%s"\n' % ("0" * 40)
    )
    assert any("duplicate key" in e for e in validate_dataset(dataset_copy))


def test_fixture_dataset_yaml_matches_builder() -> None:
    # The committed fixture dataset.yaml must equal the builder's literal, so the
    # hand-committed copy and the generator can't drift (review nit).
    b = _load_builder()
    assert (FIXTURE / "dataset.yaml").read_text() == b.DATASET_YAML_TEXT


def test_fixture_site_prose_matches_builder() -> None:
    b = _load_builder()
    for name, content in b.SITE_PROSE.items():
        assert (FIXTURE / "site" / name).read_text() == content


# --- retired ids (retired_ids.yaml) — S6.3 --------------------------------


def test_missing_retired_ids_yaml(dataset_copy: Path) -> None:
    (dataset_copy / "retired_ids.yaml").unlink()
    errs = validate_dataset(dataset_copy)
    assert any("retired_ids.yaml" in e and "missing required file" in e for e in errs)


def test_empty_retired_ids_is_noop(dataset_copy: Path) -> None:
    # The fixture's `{}` retired_ids.yaml leaves the dataset valid — an empty
    # retired set is a no-op over the existing active-only counter check.
    assert validate_dataset(dataset_copy) == []


def test_retired_id_also_active(dataset_copy: Path) -> None:
    # reach id 1 is an active row — it may not also be recorded as retired.
    (dataset_copy / "retired_ids.yaml").write_text("reach:\n  - 1\n")
    errs = validate_dataset(dataset_copy)
    assert any("reach" in e and "both active and retired" in e for e in errs)


def test_retired_id_above_counter(dataset_copy: Path) -> None:
    # A retired id at/above next_id (reach next_id=4) is a stale counter: the id
    # must stay reserved, so next_id must exceed every active-or-retired id.
    (dataset_copy / "retired_ids.yaml").write_text("reach:\n  - 9\n")
    errs = validate_dataset(dataset_copy)
    assert any("reach" in e and "next_id" in e and "active or retired" in e for e in errs)


def test_retired_non_id_bearing_table(dataset_copy: Path) -> None:
    # gauge_source is a junction (no `id` PK) — it can't carry retired ids.
    (dataset_copy / "retired_ids.yaml").write_text("gauge_source:\n  - 1\n")
    errs = validate_dataset(dataset_copy)
    assert any("not an id-bearing table" in e for e in errs)


def test_retired_value_not_a_list(dataset_copy: Path) -> None:
    (dataset_copy / "retired_ids.yaml").write_text("reach: 5\n")
    errs = validate_dataset(dataset_copy)
    assert any("value must be a list of ids" in e for e in errs)


def test_retired_id_non_integer(dataset_copy: Path) -> None:
    (dataset_copy / "retired_ids.yaml").write_text('reach:\n  - "x"\n')
    errs = validate_dataset(dataset_copy)
    assert any("retired id must be an integer" in e for e in errs)


def test_retired_id_boolean_rejected(dataset_copy: Path) -> None:
    # bool is an int subclass — `true` is not a valid retired id.
    (dataset_copy / "retired_ids.yaml").write_text("reach:\n  - true\n")
    errs = validate_dataset(dataset_copy)
    assert any("retired id must be an integer" in e for e in errs)


def test_retired_id_duplicate(dataset_copy: Path) -> None:
    (dataset_copy / "retired_ids.yaml").write_text("reach:\n  - 9\n  - 9\n")
    errs = validate_dataset(dataset_copy)
    assert any("duplicate retired ids" in e for e in errs)


def test_retired_ids_duplicate_table_key(dataset_copy: Path) -> None:
    # A repeated table key is malformed (the strict loader rejects last-wins).
    (dataset_copy / "retired_ids.yaml").write_text("reach:\n  - 9\nreach:\n  - 10\n")
    errs = validate_dataset(dataset_copy)
    assert any("duplicate key" in e for e in errs)


def test_retired_ids_unhashable_key_is_focused_error(dataset_copy: Path) -> None:
    # A YAML complex key must yield a focused error, not crash the validator
    # (the strict loader's crash-safe contract).
    (dataset_copy / "retired_ids.yaml").write_text("? [1, 2]\n: foo\n")
    errs = validate_dataset(dataset_copy)
    assert any("retired_ids.yaml" in e and "invalid YAML" in e for e in errs)


def test_fixture_retired_ids_matches_builder() -> None:
    # The committed fixture retired_ids.yaml must equal the builder's literal.
    b = _load_builder()
    assert (FIXTURE / "retired_ids.yaml").read_text() == b.RETIRED_IDS_YAML_TEXT


# --------------------------------------------------------------------------- #
# Regression report content (S2): provenance_slug ↔ report + sidecar integrity.
# The fixture ships a declared slug (`fixture_calc_from_usgs`) + its triple plus a
# link-reachable lead/lag companion under tests/fixtures/dataset/regression/.
# --------------------------------------------------------------------------- #


def _clear_provenance_slug(d: Path) -> None:
    _rewrite_csv(
        d / "calc_expression.csv",
        lambda rows: [r.update(provenance_slug="") for r in rows],
    )


def test_regression_fixture_has_no_orphan_warnings() -> None:
    # The committed fixture's lead/lag companion is reachable via the main
    # report's link, so it must NOT warn as an orphan.
    warnings: list[str] = []
    assert validate_dataset(FIXTURE, warnings) == []
    assert warnings == []


def test_regression_none_configured_is_clean(dataset_copy: Path) -> None:
    # No slugs and no regression/ dir → "none configured", not an error.
    _clear_provenance_slug(dataset_copy)
    shutil.rmtree(dataset_copy / "regression")
    warnings: list[str] = []
    assert validate_dataset(dataset_copy, warnings) == []
    assert warnings == []


def test_regression_missing_declared_md_errors(dataset_copy: Path) -> None:
    (dataset_copy / "regression" / "fixture_calc_from_usgs.md").unlink()
    errs = validate_dataset(dataset_copy)
    assert any("fixture_calc_from_usgs.md missing" in e for e in errs)


def test_regression_missing_primary_sidecar_errors(dataset_copy: Path) -> None:
    (dataset_copy / "regression" / "fixture_calc_from_usgs.json").unlink()
    errs = validate_dataset(dataset_copy)
    assert any("fixture_calc_from_usgs.json missing" in e for e in errs)


def test_regression_missing_linked_companion_errors(dataset_copy: Path) -> None:
    # The main report links to the lead/lag companion .md; removing it is an error.
    (dataset_copy / "regression" / "fixture_calc_from_usgs_leadlag.md").unlink()
    errs = validate_dataset(dataset_copy)
    assert any("fixture_calc_from_usgs_leadlag.md" in e and "does not exist" in e for e in errs)


def test_regression_missing_dir_with_declared_slug_warns_not_errors(dataset_copy: Path) -> None:
    # Transitional (pre file-move) state: slugs declared but no regression/ dir yet.
    # Must NOT fail the deploy-time validator (deploy.sh runs it on the real dataset
    # before D1 lands the files) — it is a non-fatal warning, not an error.
    shutil.rmtree(dataset_copy / "regression")
    warnings: list[str] = []
    errs = validate_dataset(dataset_copy, warnings)
    assert errs == []
    assert any("no regression/ directory" in w for w in warnings)


def test_regression_invalid_slug_errors(dataset_copy: Path) -> None:
    _rewrite_csv(
        dataset_copy / "calc_expression.csv",
        lambda rows: [r.update(provenance_slug="bad slug!") for r in rows],
    )
    errs = validate_dataset(dataset_copy)
    assert any("is not a valid slug" in e for e in errs)


def test_regression_orphan_report_warns_not_errors(dataset_copy: Path) -> None:
    # A report file referenced by no slug is advisory (warning), not fatal.
    (dataset_copy / "regression" / "stray_unused.md").write_text("# stray\n")
    warnings: list[str] = []
    errs = validate_dataset(dataset_copy, warnings)
    assert errs == []  # orphan does not fail the dataset
    assert any("stray_unused.md" in w and "orphan" in w for w in warnings)


def test_regression_malicious_svg_errors(dataset_copy: Path) -> None:
    (dataset_copy / "regression" / "fixture_calc_from_usgs.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    )
    errs = validate_dataset(dataset_copy)
    assert any("fixture_calc_from_usgs.svg" in e for e in errs)


def test_regression_referenced_sidecar_missing_errors(dataset_copy: Path) -> None:
    # The main report's svg is referenced by both the image embed and the primary
    # triple; deleting it is a hard error (a published report would 404 its plot).
    (dataset_copy / "regression" / "fixture_calc_from_usgs.svg").unlink()
    errs = validate_dataset(dataset_copy)
    assert any("fixture_calc_from_usgs.svg" in e for e in errs)


@pytest.mark.parametrize("slugs", [("aaa", "zzz"), ("zzz", "aaa")])
def test_regression_closure_requires_triple_regardless_of_link_order(
    tmp_path: Path, slugs: tuple[str, str]
) -> None:
    # Two provenance slugs where one links the other; the linked slug ships only
    # its .md (no .svg/.json). Its triple must be required no matter the BFS
    # traversal order (regression guard for the LIFO-ordering gap).
    reg = tmp_path / "regression"
    reg.mkdir()
    (reg / "aaa.md").write_text("# aaa\n")  # linked slug — missing its sidecars
    (reg / "zzz.md").write_text("# zzz\n\n[see](./aaa.md)\n")
    (reg / "zzz.svg").write_text("<svg/>")
    (reg / "zzz.json").write_text('{"slug":"zzz"}')
    _reachable, errors = _regression_closure(reg, set(slugs))
    assert any("aaa.svg missing" in e for e in errors)
    assert any("aaa.json missing" in e for e in errors)


def test_regression_closure_ignores_refs_inside_code_fences(tmp_path: Path) -> None:
    # A ](./ghost.md) shown as an example inside a fenced code block is NOT a real
    # cross-reference and must not require ghost.md to exist.
    reg = tmp_path / "regression"
    reg.mkdir()
    (reg / "r.md").write_text(
        "# r\n\nReal docs.\n\n```\nLink syntax: [x](./ghost.md) and ![y](./ghost.svg)\n```\n"
    )
    (reg / "r.svg").write_text("<svg/>")
    (reg / "r.json").write_text('{"slug":"r"}')
    _reachable, errors = _regression_closure(reg, {"r"})
    assert errors == []


def test_strip_code_fences_removes_fenced_blocks() -> None:
    text = "keep1\n```\nfenced [x](./y.md)\n```\nkeep2\n"
    out = _strip_code_fences(text)
    assert "keep1" in out and "keep2" in out
    assert "fenced" not in out and "./y.md" not in out


def test_strip_code_fences_handles_tilde_and_indented_fences() -> None:
    # ~~~ fences are stripped; a ``` line inside a ~~~ block does not close it,
    # so a real link AFTER the block survives the scan.
    text = "~~~\n```\n[hidden](./h.md)\n~~~\n[real](./r.md)\n"
    out = _strip_code_fences(text)
    assert "./h.md" not in out  # inside the ~~~ block
    assert "./r.md" in out  # after the block — a real reference, preserved
    # A 4-space-indented ``` is an indented code block, not a fence (CommonMark),
    # so it must NOT open a fence that swallows the following real link.
    text2 = "    ```\n[real2](./r2.md)\n"
    assert "./r2.md" in _strip_code_fences(text2)


# --------------------------------------------------------------------------- #
# site.yaml — opt-in dataset identity (S3a)
# --------------------------------------------------------------------------- #


def test_site_yaml_absent_is_valid(dataset_copy: Path) -> None:
    # The fixture ships no site.yaml — opt-in, so the dataset is still valid.
    assert not (dataset_copy / "site.yaml").exists()
    assert validate_dataset(dataset_copy) == []


def test_valid_site_yaml_accepted(dataset_copy: Path) -> None:
    (dataset_copy / "site.yaml").write_text(
        'site_name: Foo Levels\norg_name: Foo Paddlers\nbrand_color: "#abcdef"\n'
    )
    assert validate_dataset(dataset_copy) == []


def test_malformed_site_yaml_rejected(dataset_copy: Path) -> None:
    # A bad color must fail the deploy gate (both the build + PHP render it).
    (dataset_copy / "site.yaml").write_text("brand_color: not-a-color\n")
    errs = validate_dataset(dataset_copy)
    assert any("site.yaml" in e and "hex color" in e for e in errs)


def test_site_yaml_unknown_key_rejected(dataset_copy: Path) -> None:
    (dataset_copy / "site.yaml").write_text("site_name: X\nbogus: 1\n")
    errs = validate_dataset(dataset_copy)
    assert any("site.yaml" in e for e in errs)


def test_site_yaml_non_string_key_reports_not_crashes(dataset_copy: Path) -> None:
    # A non-string YAML key must surface as a validation error, not crash the
    # validator with a TypeError (PR #155 review — _check_site_yaml caught only
    # ValueError, so SiteConfig(**{1: ...})'s TypeError escaped).
    (dataset_copy / "site.yaml").write_text("1: foo\n")
    errs = validate_dataset(dataset_copy)  # must not raise
    assert any("site.yaml" in e and "non-string key" in e for e in errs)


# --------------------------------------------------------------------------- #
# region.yaml — opt-in per-state links/weather (S3b)
# --------------------------------------------------------------------------- #


def test_region_yaml_absent_is_valid(dataset_copy: Path) -> None:
    assert not (dataset_copy / "region.yaml").exists()
    assert validate_dataset(dataset_copy) == []


def test_valid_region_yaml_accepted(dataset_copy: Path) -> None:
    (dataset_copy / "region.yaml").write_text(
        "states:\n  Washington:\n    weather_url: https://example.com/wa\n"
        "    links:\n      - {label: Foo, url: https://foo.example}\n"
    )
    assert validate_dataset(dataset_copy) == []


def test_malformed_region_yaml_rejected(dataset_copy: Path) -> None:
    (dataset_copy / "region.yaml").write_text(
        "states:\n  Oregon:\n    links:\n      - {label: X, url: not-a-url}\n"
    )
    errs = validate_dataset(dataset_copy)
    assert any("region.yaml" in e and "http" in e for e in errs)


def test_region_yaml_unsafe_state_key_rejected(dataset_copy: Path) -> None:
    # A region state key becomes a filename/URL/HTML in the build — a path-traversal
    # key must fail the deploy gate (#160 review — High).
    (dataset_copy / "region.yaml").write_text("states:\n  ../escaped:\n    links: []\n")
    errs = validate_dataset(dataset_copy)
    assert any("region.yaml" in e and "safe name" in e for e in errs)


def test_state_csv_unsafe_name_rejected(dataset_copy: Path) -> None:
    # state.csv names also flow to filenames/URLs/HTML now (S3b-2) — gate them too.
    def _mutate(rows: list[dict[str, str]]) -> None:
        rows[0]["name"] = "../escaped"

    _rewrite_csv(dataset_copy / "state.csv", _mutate)
    errs = validate_dataset(dataset_copy)
    assert any("state.csv" in e and "unsafe state name" in e for e in errs)


def test_state_csv_trailing_space_name_rejected(dataset_copy: Path) -> None:
    # Exact check on the RAW cell: the importer stores "Oregon " unstripped, so the
    # build would emit /Oregon%20.html — the gate must catch it (review).
    def _mutate(rows: list[dict[str, str]]) -> None:
        rows[0]["name"] = rows[0]["name"] + " "

    _rewrite_csv(dataset_copy / "state.csv", _mutate)
    errs = validate_dataset(dataset_copy)
    assert any("state.csv" in e and "unsafe state name" in e for e in errs)


def test_state_csv_whitespace_only_name_rejected(dataset_copy: Path) -> None:
    def _mutate(rows: list[dict[str, str]]) -> None:
        rows[0]["name"] = "   "

    _rewrite_csv(dataset_copy / "state.csv", _mutate)
    errs = validate_dataset(dataset_copy)
    assert any("state.csv" in e and "unsafe state name" in e for e in errs)


def test_gauge_state_abbreviation_must_exist_in_state_csv(dataset_copy: Path) -> None:
    def _mutate(rows: list[dict[str, str]]) -> None:
        rows[0]["state"] = "OR,ZZ"

    _rewrite_csv(dataset_copy / "gauge.csv", _mutate)
    errs = validate_dataset(dataset_copy)
    assert any("gauge.csv" in e and "ZZ" in e and "state.csv" in e for e in errs)


def test_gauge_state_abbreviation_rejected_when_state_csv_empty(dataset_copy: Path) -> None:
    _rewrite_csv(dataset_copy / "state.csv", lambda rows: rows.clear())

    errs = validate_dataset(dataset_copy)
    assert any("gauge.csv" in e and "state.csv" in e for e in errs)


def test_region_yaml_state_must_exist_in_state_csv(dataset_copy: Path) -> None:
    (dataset_copy / "region.yaml").write_text("states:\n  Atlantis:\n    links: []\n")

    errs = validate_dataset(dataset_copy)
    assert any("region.yaml" in e and "Atlantis" in e and "state.csv" in e for e in errs)


# --------------------------------------------------------------------------- #
# site/*.md prose — legal trio required when publishable (S3c/S3i)
# --------------------------------------------------------------------------- #


def test_scaffold_site_prose_absent_is_valid(dataset_copy: Path) -> None:
    shutil.rmtree(dataset_copy / "site")
    _set_dataset_status(dataset_copy, "scaffold")
    assert validate_dataset(dataset_copy) == []


def test_publishable_site_prose_absent_requires_legal_prose(dataset_copy: Path) -> None:
    shutil.rmtree(dataset_copy / "site")
    errs = validate_dataset(dataset_copy)
    assert any("site/privacy.md is required" in e for e in errs)
    assert any("site/disclaimer.md is required" in e for e in errs)
    assert any("site/contact.md is required" in e for e in errs)


def test_publishable_with_site_dir_requires_legal_prose(dataset_copy: Path) -> None:
    # A publishable dataset must carry the full legal set; a half-moved set
    # (only about) is rejected.
    shutil.rmtree(dataset_copy / "site")
    (dataset_copy / "site").mkdir()
    (dataset_copy / "site" / "about.md").write_text("## About\n\nHi.\n")
    errs = validate_dataset(dataset_copy)
    assert any("site/privacy.md is required" in e for e in errs)
    assert any("site/disclaimer.md is required" in e for e in errs)
    assert any("site/contact.md is required" in e for e in errs)


def test_complete_site_prose_accepted(dataset_copy: Path) -> None:
    shutil.rmtree(dataset_copy / "site")
    (dataset_copy / "site").mkdir()
    for page in ("about", "disclaimer", "privacy", "contact"):
        (dataset_copy / "site" / f"{page}.md").write_text(f"## {page.title()}\n\nText.\n")
    assert validate_dataset(dataset_copy) == []


def test_site_prose_rejects_site_file(dataset_copy: Path) -> None:
    shutil.rmtree(dataset_copy / "site")
    (dataset_copy / "site").write_text("not a directory\n", encoding="utf-8")
    errs = validate_dataset(dataset_copy)
    assert any(e == "site: must be a directory" for e in errs)


def test_site_prose_rejects_symlinked_site_dir(dataset_copy: Path, tmp_path: Path) -> None:
    shutil.rmtree(dataset_copy / "site")
    outside = tmp_path / "outside-site"
    outside.mkdir()
    (dataset_copy / "site").symlink_to(outside)

    errs = validate_dataset(dataset_copy)
    assert any(e == "site: symlinks are not supported" for e in errs)


def test_site_prose_rejects_symlinked_page(dataset_copy: Path, tmp_path: Path) -> None:
    target = tmp_path / "privacy.md"
    target.write_text("# Outside\n\nNot dataset-owned.\n", encoding="utf-8")
    (dataset_copy / "site" / "privacy.md").unlink()
    (dataset_copy / "site" / "privacy.md").symlink_to(target)

    errs = validate_dataset(dataset_copy)
    assert any(e == "site/privacy.md: symlinks are not supported" for e in errs)


def test_site_prose_rejects_dangling_symlinked_site_dir(dataset_copy: Path, tmp_path: Path) -> None:
    shutil.rmtree(dataset_copy / "site")
    (dataset_copy / "site").symlink_to(tmp_path / "missing-site")

    errs = validate_dataset(dataset_copy)
    assert any(e == "site: symlinks are not supported" for e in errs)


def test_site_assets_valid_set_accepted(dataset_copy: Path) -> None:
    _write_required_site_prose(dataset_copy)
    assets = dataset_copy / "site" / "assets"
    assets.mkdir()
    (assets / "README.md").write_text("# Assets\n", encoding="utf-8")
    (assets / "favicon.ico").write_bytes(_ico_bytes(32, 32))
    (assets / "icon-180.png").write_bytes(_png_bytes(180, 180))
    (assets / "icon-192.png").write_bytes(_png_bytes(192, 192))
    (assets / "og-image.png").write_bytes(_png_bytes(1200, 630))

    assert validate_dataset(dataset_copy) == []


def test_site_assets_reject_unexpected_file(dataset_copy: Path) -> None:
    _write_required_site_prose(dataset_copy)
    assets = dataset_copy / "site" / "assets"
    assets.mkdir()
    (assets / "script.js").write_text("alert(1)\n", encoding="utf-8")

    errs = validate_dataset(dataset_copy)
    assert any("site/assets/script.js" in e and "unexpected site asset" in e for e in errs)


def test_site_assets_reject_wrong_dimensions(dataset_copy: Path) -> None:
    _write_required_site_prose(dataset_copy)
    assets = dataset_copy / "site" / "assets"
    assets.mkdir()
    (assets / "og-image.png").write_bytes(_png_bytes(1200, 600))

    errs = validate_dataset(dataset_copy)
    assert any("site/assets/og-image.png" in e and "1200x630" in e for e in errs)


def test_site_assets_reject_symlink(dataset_copy: Path, tmp_path: Path) -> None:
    _write_required_site_prose(dataset_copy)
    assets = dataset_copy / "site" / "assets"
    assets.mkdir()
    target = tmp_path / "outside.png"
    target.write_bytes(_png_bytes(1200, 630))
    (assets / "og-image.png").symlink_to(target)

    errs = validate_dataset(dataset_copy)
    assert any("site/assets/og-image.png" in e and "symlinks" in e for e in errs)


def test_site_assets_reject_symlinked_asset_dir(dataset_copy: Path, tmp_path: Path) -> None:
    _write_required_site_prose(dataset_copy)
    outside = tmp_path / "outside-assets"
    outside.mkdir()
    (dataset_copy / "site" / "assets").symlink_to(outside)

    errs = validate_dataset(dataset_copy)
    assert any(e == "site/assets: symlinks are not supported" for e in errs)


def test_site_assets_reject_dangling_symlinked_asset_dir(
    dataset_copy: Path, tmp_path: Path
) -> None:
    _write_required_site_prose(dataset_copy)
    (dataset_copy / "site" / "assets").symlink_to(tmp_path / "missing-assets")

    errs = validate_dataset(dataset_copy)
    assert any(e == "site/assets: symlinks are not supported" for e in errs)
