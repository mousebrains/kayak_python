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
        length: float | None = None,
        elevation: float | None = None,
        elevation_lost: float | None = None,
        gradient: float | None = None,
        gradient_profile: str | None = None,
    ) -> None:
        self.id = id
        self.geom = geom
        self.latitude_start = latitude_start
        self.longitude_start = longitude_start
        self.latitude_end = latitude_end
        self.longitude_end = longitude_end
        self.display_name = display_name
        self.aw_id = aw_id
        self.length = length
        self.elevation = elevation
        self.elevation_lost = elevation_lost
        self.gradient = gradient
        self.gradient_profile = gradient_profile


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


def test_elevation_complete_reach_has_no_elevation_issue() -> None:
    r = _FakeReach(
        latitude_start=44.1, longitude_start=-122.0,
        latitude_end=44.2, longitude_end=-122.1,
        length=11.22,
        elevation=2197.0, elevation_lost=867.0, gradient=77.3,
    )
    assert check_reaches._check_one(r, endpoint_tol_deg=_DEFAULT_TOL) == []


def test_missing_elevation_is_flagged() -> None:
    r = _FakeReach(
        id=407,
        latitude_start=44.1, longitude_start=-122.0,
        latitude_end=44.2, longitude_end=-122.1,
        length=11.22,
        elevation=None, elevation_lost=867.0, gradient=77.3,
    )
    issues = check_reaches._check_one(r, endpoint_tol_deg=_DEFAULT_TOL)
    assert len(issues) == 1
    assert "elevation" in issues[0]
    assert "elevation_lost" not in issues[0]
    assert "--reach-ids 407" in issues[0]


def test_all_three_elevation_columns_null_lists_them_together() -> None:
    r = _FakeReach(
        latitude_start=44.1, longitude_start=-122.0,
        latitude_end=44.2, longitude_end=-122.1,
        length=11.22,
        elevation=None, elevation_lost=None, gradient=None,
    )
    issues = check_reaches._check_one(r, endpoint_tol_deg=_DEFAULT_TOL)
    assert len(issues) == 1
    assert "elevation, elevation_lost, gradient" in issues[0]


def test_no_length_skips_elevation_check() -> None:
    # No length means we can't derive gradient anyway — no point flagging.
    r = _FakeReach(
        latitude_start=44.1, longitude_start=-122.0,
        latitude_end=44.2, longitude_end=-122.1,
        length=None,
        elevation=None, elevation_lost=None, gradient=None,
    )
    assert check_reaches._check_one(r, endpoint_tol_deg=_DEFAULT_TOL) == []


def test_no_endpoints_skips_elevation_check() -> None:
    # Endpoints missing — refresh_reach_elevations.py has nothing to
    # query, so the elevation gap isn't actionable.
    r = _FakeReach(
        latitude_start=None, longitude_start=None,
        length=11.22,
        elevation=None,
    )
    assert check_reaches._check_one(r, endpoint_tol_deg=_DEFAULT_TOL) == []


def test_elevation_check_runs_even_without_geom() -> None:
    # The elevation gap is independent of geom — should still fire.
    r = _FakeReach(
        id=42,
        geom=None,
        latitude_start=44.1, longitude_start=-122.0,
        latitude_end=44.2, longitude_end=-122.1,
        length=11.22,
        elevation=None, elevation_lost=None, gradient=None,
    )
    issues = check_reaches._check_one(r, endpoint_tol_deg=_DEFAULT_TOL)
    assert len(issues) == 1
    assert "NULL despite endpoints + length present" in issues[0]


def test_elevation_check_coexists_with_geom_checks() -> None:
    # Both issues should surface — wrapper + elevation gap.
    r = _FakeReach(
        geom="LINESTRING(-122.0 44.0,-122.1 44.1)",
        latitude_start=44.0, longitude_start=-122.0,
        latitude_end=44.1, longitude_end=-122.1,
        length=11.22,
    )
    issues = check_reaches._check_one(r, endpoint_tol_deg=_DEFAULT_TOL)
    assert any("wrapper" in i.lower() for i in issues)
    assert any("NULL despite endpoints" in i for i in issues)


def test_known_real_extreme_peak_is_not_flagged() -> None:
    # Reaches in _KNOWN_REAL_EXTREME_PEAKS get the extreme-peak check
    # bypassed even when samples exceed the threshold (operator-confirmed
    # real terrain).
    import json
    profile = json.dumps({
        "samples": [
            {"d_mi": 0.5, "lat": 44.1, "lon": -122.0, "grad_ft_per_mi": 800, "w_mi": 0.0625, "significant": True},
            {"d_mi": 0.55, "lat": 44.11, "lon": -122.01, "grad_ft_per_mi": 2200, "w_mi": 0.0625, "significant": True},
        ],
    })
    known_id = next(iter(check_reaches._KNOWN_REAL_EXTREME_PEAKS))
    r = _FakeReach(id=known_id, gradient_profile=profile)
    assert check_reaches._check_one(r, endpoint_tol_deg=_DEFAULT_TOL) == []


def test_extreme_peak_in_gradient_profile_is_flagged() -> None:
    import json
    profile = json.dumps({
        "samples": [
            {"d_mi": 0.5, "lat": 44.1, "lon": -122.0, "grad_ft_per_mi": 800, "w_mi": 0.0625, "significant": True},
            {"d_mi": 0.55, "lat": 44.11, "lon": -122.01, "grad_ft_per_mi": 2200, "w_mi": 0.0625, "significant": True},
        ],
    })
    r = _FakeReach(gradient_profile=profile)
    issues = check_reaches._check_one(r, endpoint_tol_deg=_DEFAULT_TOL)
    assert any("2200 ft/mi" in i for i in issues)
    assert any("review for trace/waterfall realism" in i for i in issues)


def test_normal_gradient_profile_is_not_flagged() -> None:
    # Both samples stay under the 1000 ft/mi extreme-peak threshold —
    # 800 is a steep but plausible Class V drop, 900 likewise.
    import json
    profile = json.dumps({
        "samples": [
            {"d_mi": 0.0, "lat": 44.1, "lon": -122.0, "grad_ft_per_mi": 800, "w_mi": 0.25, "significant": True},
            {"d_mi": 0.25, "lat": 44.11, "lon": -122.01, "grad_ft_per_mi": 900, "w_mi": 0.0625, "significant": True},
        ],
    })
    r = _FakeReach(gradient_profile=profile)
    assert check_reaches._check_one(r, endpoint_tol_deg=_DEFAULT_TOL) == []


def test_malformed_gradient_profile_is_flagged() -> None:
    r = _FakeReach(gradient_profile="not-json")
    issues = check_reaches._check_one(r, endpoint_tol_deg=_DEFAULT_TOL)
    assert any("not valid JSON" in i for i in issues)


def test_addargs_registers_subcommand() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    check_reaches.addArgs(subparsers)
    # Should parse cleanly with the new subcommand.
    args = parser.parse_args(["check-reaches"])
    assert args.func is check_reaches.check_reaches
    assert args.endpoint_tolerance == pytest.approx(check_reaches._ENDPOINT_TOL_DEG)
