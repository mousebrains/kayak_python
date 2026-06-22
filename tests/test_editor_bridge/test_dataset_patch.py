"""Tests for the dataset patch adapters (Tier 3 of the editor → kayak_data bridge).

Pure CSV-by-id editing: allowlist enforcement, minimal diffs (one row's line),
the reach updated_at stamp, drift/conflict detection, and the deferred /
unsupported targets. No git/network/DB.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from kayak.editor_bridge.dataset_patch import (
    ConflictError,
    DatasetPatchError,
    apply_change,
)

_REACH_HEADER = [
    "id",
    "updated_at",
    "gauge_id",
    "name",
    "display_name",
    "description",
    "features",
    "latitude_start",
    "longitude_start",
    "latitude_end",
    "longitude_end",
    "notes",
    "length",
]
_GAUGE_HEADER = ["id", "name", "display_name", "location", "latitude", "longitude"]


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    _write_csv(
        tmp_path / "reach.csv",
        _REACH_HEADER,
        [
            # id, updated_at, gauge_id, name, display_name, description, features,
            # lat_start, lon_start, lat_end, lon_end, notes, length
            [
                "1",
                "2026-01-01",
                "10",
                "alpha",
                "Alpha Run",
                "old desc 1",
                "f1",
                "1.1",
                "2.2",
                "3.3",
                "4.4",
                "n1",
                "1.0",
            ],
            [
                "2",
                "2026-01-01",
                "11",
                "bravo",
                "Bravo Run",
                "old desc 2",
                "f2",
                "5.5",
                "6.6",
                "7.7",
                "8.8",
                "n2",
                "2.0",
            ],
            [
                "3",
                "2026-01-01",
                "12",
                "charlie",
                "Charlie Run",
                "old desc 3",
                "f3",
                "9.9",
                "1.0",
                "1.1",
                "1.2",
                "n3",
                "3.0",
            ],
        ],
    )
    _write_csv(
        tmp_path / "gauge.csv",
        _GAUGE_HEADER,
        [
            ["100", "G100", "Gauge 100 (calc)", "Somewhere", "44.0", "-122.0"],
            ["101", "G101", "Gauge 101", "Elsewhere", "45.0", "-123.0"],
        ],
    )
    return tmp_path


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def _row(path: Path, target_id: str) -> dict[str, str]:
    with path.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            if r["id"] == target_id:
                return r
    raise AssertionError(f"id {target_id} not found")


# ---------------------------------------------------------------------------
# reach
# ---------------------------------------------------------------------------


def test_apply_reach_patches_one_cell_minimal_diff(dataset):
    before = _lines(dataset / "reach.csv")
    results = apply_change(
        dataset, "reach", 2, {"reach": {"description": "new desc"}}, updated_at="2026-06-21"
    )
    after = _lines(dataset / "reach.csv")

    assert len(before) == len(after)
    diff_idx = [i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b]
    assert diff_idx == [2]  # only reach id 2's line (header=0, id1=1, id2=2)

    r = _row(dataset / "reach.csv", "2")
    assert r["description"] == "new desc"
    assert r["updated_at"] == "2026-06-21"  # stamped
    # siblings untouched
    assert _row(dataset / "reach.csv", "1")["description"] == "old desc 1"
    assert _row(dataset / "reach.csv", "3")["updated_at"] == "2026-01-01"

    (res,) = results
    assert res.file == "reach.csv"
    assert res.changed["description"] == ("old desc 2", "new desc")
    assert res.changed["updated_at"] == ("2026-01-01", "2026-06-21")


def test_apply_reach_multi_field(dataset):
    apply_change(
        dataset,
        "reach",
        1,
        {"reach": {"description": "d", "features": "ff", "display_name": "Alpha!"}},
        updated_at="2026-06-21",
    )
    r = _row(dataset / "reach.csv", "1")
    assert (r["description"], r["features"], r["display_name"]) == ("d", "ff", "Alpha!")
    assert r["updated_at"] == "2026-06-21"


def test_apply_reach_noop_does_not_stamp_or_write(dataset):
    before = dataset.joinpath("reach.csv").read_bytes()
    (res,) = apply_change(
        dataset, "reach", 2, {"reach": {"description": "old desc 2"}}, updated_at="2026-09-09"
    )
    assert res.is_noop
    assert (
        dataset.joinpath("reach.csv").read_bytes() == before
    )  # byte-identical, no updated_at churn


def test_apply_reach_value_with_comma_is_quoted_one_line(dataset):
    apply_change(
        dataset,
        "reach",
        3,
        {"reach": {"description": "Put-in, then, take-out"}},
        updated_at="2026-06-21",
    )
    assert _row(dataset / "reach.csv", "3")["description"] == "Put-in, then, take-out"
    # still one physical line per row (the comma'd value got quoted, not split)
    text = (dataset / "reach.csv").read_text()
    assert len(text.splitlines()) == 4  # header + 3 rows


def test_apply_reach_rejects_unknown_field(dataset):
    with pytest.raises(DatasetPatchError, match="not allowed"):
        apply_change(dataset, "reach", 2, {"reach": {"name": "hax"}}, updated_at="x")


def test_apply_reach_coerces_numeric_coordinate(dataset):
    # PHP casts coordinate fields to float, so applied_json carries a JSON number;
    # the adapter renders it to the text cell (the central "full"-tier edit).
    (res,) = apply_change(
        dataset, "reach", 2, {"reach": {"latitude_start": 45.123456}}, updated_at="2026-06-21"
    )
    assert _row(dataset / "reach.csv", "2")["latitude_start"] == "45.123456"
    assert res.changed["latitude_start"] == ("5.5", "45.123456")


def test_apply_reach_accepts_edit_php_fields(dataset):
    # edit.php (maintainer direct edit) can change a broader reach set than
    # propose; a numeric one (length) arrives as a float.
    apply_change(
        dataset,
        "reach",
        1,
        {"reach": {"notes": "scout the drop", "length": 4.2}},
        updated_at="2026-06-21",
    )
    r = _row(dataset / "reach.csv", "1")
    assert (r["notes"], r["length"]) == ("scout the drop", "4.2")


def test_apply_reach_rejects_bool_value(dataset):
    with pytest.raises(DatasetPatchError, match="boolean"):
        apply_change(dataset, "reach", 2, {"reach": {"description": True}}, updated_at="x")


def test_apply_reach_id_not_found(dataset):
    with pytest.raises(DatasetPatchError, match="id 999 not found"):
        apply_change(dataset, "reach", 999, {"reach": {"description": "x"}}, updated_at="x")


def test_apply_reach_empty_diff_rejected(dataset):
    with pytest.raises(DatasetPatchError, match="non-empty"):
        apply_change(dataset, "reach", 2, {"reach": {}}, updated_at="x")


def test_apply_change_reach_class_unsupported(dataset):
    with pytest.raises(DatasetPatchError, match="reach_class"):
        apply_change(
            dataset,
            "reach",
            2,
            {"reach_class": {"names": ["III"], "range": {}}},
            updated_at="x",
        )


# ---------------------------------------------------------------------------
# gauge
# ---------------------------------------------------------------------------


def test_apply_gauge_patches_location(dataset):
    before = _lines(dataset / "gauge.csv")
    (res,) = apply_change(
        dataset, "gauge", 100, {"gauge": {"location": "New Spot"}}, updated_at="ignored"
    )
    after = _lines(dataset / "gauge.csv")
    diff_idx = [i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b]
    assert diff_idx == [1]  # only gauge 100's line
    assert _row(dataset / "gauge.csv", "100")["location"] == "New Spot"
    assert res.changed == {"location": ("Somewhere", "New Spot")}


def test_apply_gauge_coerces_numeric(dataset):
    apply_change(dataset, "gauge", 101, {"gauge": {"latitude": 45.5}}, updated_at="x")
    assert _row(dataset / "gauge.csv", "101")["latitude"] == "45.5"


def test_apply_gauge_rejects_field_not_a_column(dataset):
    with pytest.raises(DatasetPatchError, match="not allowed"):
        apply_change(dataset, "gauge", 100, {"gauge": {"bogus": "x"}}, updated_at="x")


def test_apply_gauge_rejects_real_but_non_editable_column(dataset):
    # display_name is a real gauge.csv column but NOT in edit.php's editable set,
    # so the adapter's defense-in-depth allowlist rejects it (the gauge freeze
    # path has no server-side allowlist).
    with pytest.raises(DatasetPatchError, match="not allowed"):
        apply_change(dataset, "gauge", 100, {"gauge": {"display_name": "x"}}, updated_at="x")


# ---------------------------------------------------------------------------
# drift / conflict + dispatch
# ---------------------------------------------------------------------------


def test_drift_raises_conflict_before_writing(dataset):
    before = dataset.joinpath("reach.csv").read_bytes()
    with pytest.raises(ConflictError, match="drifted"):
        apply_change(
            dataset,
            "reach",
            2,
            {"reach": {"description": "new"}},
            updated_at="2026-06-21",
            expected_base={"description": "what the reviewer saw"},  # != current "old desc 2"
        )
    assert dataset.joinpath("reach.csv").read_bytes() == before  # fail-closed, no write


def test_drift_ok_when_base_matches(dataset):
    (res,) = apply_change(
        dataset,
        "reach",
        2,
        {"reach": {"description": "new"}},
        updated_at="2026-06-21",
        expected_base={"description": "old desc 2"},  # matches current
    )
    assert res.changed["description"] == ("old desc 2", "new")


def test_apply_change_rejects_site_and_source(dataset):
    with pytest.raises(DatasetPatchError, match="unsupported target_type"):
        apply_change(dataset, "site", None, {"body": "hi"}, updated_at="x")
    with pytest.raises(DatasetPatchError, match="unsupported target_type"):
        apply_change(dataset, "source", 5, {"source": {"name": "x"}}, updated_at="x")


# ---------------------------------------------------------------------------
# robustness: ragged rows + trailing-newline preservation
# ---------------------------------------------------------------------------


def test_ragged_row_rejected(tmp_path):
    # A row with fewer cells than the header would silently drop fields on
    # rewrite — refuse loudly instead.
    (tmp_path / "reach.csv").write_text(
        "id,updated_at,description\n5,2026-01-01\n", encoding="utf-8"
    )
    with pytest.raises(DatasetPatchError, match="cells"):
        apply_change(tmp_path, "reach", 5, {"reach": {"description": "x"}}, updated_at="t")


def test_last_row_without_trailing_newline_preserved(tmp_path):
    # The final row has no trailing newline; patching it must not add one (and
    # must leave the header + sibling row byte-identical).
    (tmp_path / "reach.csv").write_text(
        "id,updated_at,description\n1,2026-01-01,a\n2,2026-01-01,b", encoding="utf-8"
    )
    apply_change(tmp_path, "reach", 2, {"reach": {"description": "B"}}, updated_at="2026-06-21")
    text = (tmp_path / "reach.csv").read_text(encoding="utf-8")
    assert not text.endswith("\n")  # trailing-newline state preserved
    assert text.startswith("id,updated_at,description\n1,2026-01-01,a\n")  # header + row 1 intact
    assert text.endswith("2,2026-06-21,B")
