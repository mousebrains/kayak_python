"""Tests for the ``levels check-reaches`` validator."""

from __future__ import annotations

import argparse

import pytest

from kayak.cli import check_reaches


class _FakeReach:
    """Minimal stand-in for kayak.db.models.Reach used by the validator."""

    def __init__(
        self,
        *,
        id: int = 1,
        geom: str | None = None,
        latitude_start: float | None = None,
        longitude_start: float | None = None,
        latitude_end: float | None = None,
        longitude_end: float | None = None,
        display_name: str | None = None,
        aw_id: int | None = None,
    ) -> None:
        self.id = id
        self.geom = geom
        self.latitude_start = latitude_start
        self.longitude_start = longitude_start
        self.latitude_end = latitude_end
        self.longitude_end = longitude_end
        self.display_name = display_name
        self.aw_id = aw_id


_DEFAULT_TOL = 0.003


def test_clean_reach_returns_no_issues() -> None:
    r = _FakeReach(
        geom="-122.021835 44.104762,-122.020467 44.105745,-122.018000 44.108000",
        latitude_start=44.104762,
        longitude_start=-122.021835,
        latitude_end=44.108000,
        longitude_end=-122.018000,
    )
    assert check_reaches._check_one(r, endpoint_tol_deg=_DEFAULT_TOL) == []


def test_linestring_wrapper_is_flagged() -> None:
    r = _FakeReach(geom="LINESTRING(-122.0 44.0,-122.1 44.1)")
    issues = check_reaches._check_one(r, endpoint_tol_deg=_DEFAULT_TOL)
    assert any("wrapper" in i.lower() for i in issues)


def test_out_of_range_coord_is_flagged() -> None:
    r = _FakeReach(geom="-122.0 44.0,-122.1 999.0")
    issues = check_reaches._check_one(r, endpoint_tol_deg=_DEFAULT_TOL)
    assert any("latitude" in i for i in issues)


def test_single_vertex_is_flagged() -> None:
    r = _FakeReach(geom="-122.0 44.0")
    issues = check_reaches._check_one(r, endpoint_tol_deg=_DEFAULT_TOL)
    assert any("only 1 vertex" in i for i in issues)


def test_endpoint_drift_on_start_is_flagged() -> None:
    # First geom vertex is at (44.0, -122.0); columns say (45.0, -122.0)
    # — that's 1° of latitude (~111 km) drift, way past the default 0.003°.
    r = _FakeReach(
        geom="-122.0 44.0,-122.1 44.1",
        latitude_start=45.0,
        longitude_start=-122.0,
        latitude_end=44.1,
        longitude_end=-122.1,
    )
    issues = check_reaches._check_one(r, endpoint_tol_deg=_DEFAULT_TOL)
    assert any("first geom vertex" in i for i in issues)


def test_endpoint_drift_on_end_is_flagged() -> None:
    r = _FakeReach(
        geom="-122.0 44.0,-122.1 44.1",
        latitude_start=44.0,
        longitude_start=-122.0,
        latitude_end=45.0,  # mismatched
        longitude_end=-122.1,
    )
    issues = check_reaches._check_one(r, endpoint_tol_deg=_DEFAULT_TOL)
    assert any("last geom vertex" in i for i in issues)


def test_missing_endpoint_columns_skips_drift_check() -> None:
    # latitude_start / longitude_start NULL — we can't check drift but
    # other validation still applies.
    r = _FakeReach(geom="-122.0 44.0,-122.1 44.1")
    issues = check_reaches._check_one(r, endpoint_tol_deg=_DEFAULT_TOL)
    assert issues == []


def test_decimal_endpoint_columns_work() -> None:
    # SQLAlchemy hands back Decimal for the lat/lon columns in practice;
    # the drift helper must cast to float to subtract from a parsed float.
    from decimal import Decimal

    r = _FakeReach(
        geom="-122.0 44.0,-122.1 44.1",
        latitude_start=Decimal("44.000001"),
        longitude_start=Decimal("-122.000001"),
        latitude_end=Decimal("44.100000"),
        longitude_end=Decimal("-122.100000"),
    )
    assert check_reaches._check_one(r, endpoint_tol_deg=_DEFAULT_TOL) == []


def test_empty_geom_skips_all_checks() -> None:
    r = _FakeReach(geom=None)
    assert check_reaches._check_one(r, endpoint_tol_deg=_DEFAULT_TOL) == []
    r = _FakeReach(geom="")
    assert check_reaches._check_one(r, endpoint_tol_deg=_DEFAULT_TOL) == []


def test_check_one_continues_through_wrapper_to_find_more() -> None:
    # A WKT-wrapped geom with otherwise-valid coords should report BOTH
    # the wrapper issue AND surface the parse failure that follows from
    # the wrapper, rather than aborting on the first.
    r = _FakeReach(geom="LINESTRING(-122.0 44.0,-122.1 44.1)")
    issues = check_reaches._check_one(r, endpoint_tol_deg=_DEFAULT_TOL)
    # The wrapper message must be present; the second issue (parse
    # failure due to "LINESTRING(-122.0" not being a valid number) is
    # also expected.
    assert any("wrapper" in i.lower() for i in issues)
    assert len(issues) >= 1


def test_addargs_registers_subcommand() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    check_reaches.addArgs(subparsers)
    # Should parse cleanly with the new subcommand.
    args = parser.parse_args(["check-reaches"])
    assert args.func is check_reaches.check_reaches
    assert args.endpoint_tolerance == pytest.approx(check_reaches._ENDPOINT_TOL_DEG)
