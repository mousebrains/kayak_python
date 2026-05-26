"""Tests for HUC12 watershed-code assignment.

Uses a tiny synthetic GPKG fixture (3 polygons in a row) so tests don't depend
on the multi-GB WBD download.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

geopandas = pytest.importorskip("geopandas")
pytest.importorskip("shapely")

from shapely.geometry import Polygon  # noqa: E402

from kayak.db.models import HucName, Reach  # noqa: E402
from kayak.db.reaches import get_reach_huc_counts  # noqa: E402
from kayak.huc.assign import assign_one, load_huc12, run, upsert_huc_names  # noqa: E402


@pytest.fixture(scope="session")
def synthetic_gpkg(tmp_path_factory) -> Path:
    """Build a minimal WBD-style GPKG with three side-by-side square polygons.

    Three HUC12s share the same HUC8 prefix '17091234'; HUC10 prefixes split
    them. This is enough to exercise containment, miss, and the level/name
    upsert across HUC8/10/12 layers.
    """
    out = tmp_path_factory.mktemp("wbd") / "wbd.gpkg"
    polys = [
        Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),  # x in [0,1)
        Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),  # x in [1,2)
        Polygon([(2, 0), (3, 0), (3, 1), (2, 1)]),  # x in [2,3)
    ]
    huc12s = ["170912340101", "170912340102", "170912340201"]
    names12 = ["West Cell", "Middle Cell", "East Cell"]

    gdf12 = geopandas.GeoDataFrame(
        {"HUC12": huc12s, "Name": names12, "States": ["OR", "OR", "OR"]},
        geometry=polys,
        crs="EPSG:4326",
    )
    gdf12.to_file(out, layer="WBDHU12", driver="GPKG")

    # Two HUC10s (one collapses two HUC12s, one is a single HUC12).
    huc10s = ["1709123401", "1709123402"]
    poly10s = [
        Polygon([(0, 0), (2, 0), (2, 1), (0, 1)]),
        Polygon([(2, 0), (3, 0), (3, 1), (2, 1)]),
    ]
    gdf10 = geopandas.GeoDataFrame(
        {"HUC10": huc10s, "Name": ["West Sub", "East Sub"], "States": ["OR", "OR"]},
        geometry=poly10s,
        crs="EPSG:4326",
    )
    gdf10.to_file(out, layer="WBDHU10", driver="GPKG")

    # One HUC8 covering the full extent.
    full = Polygon([(0, 0), (3, 0), (3, 1), (0, 1)])
    gdf8 = geopandas.GeoDataFrame(
        {"HUC8": ["17091234"], "Name": ["Synthetic Basin"], "States": ["OR"]},
        geometry=[full],
        crs="EPSG:4326",
    )
    gdf8.to_file(out, layer="WBDHU8", driver="GPKG")

    # HUC2/4/6 all single polygons covering the same extent for this fixture.
    for layer, code_col, code, label in (
        ("WBDHU6", "HUC6", "170912", "Synthetic Region 6"),
        ("WBDHU4", "HUC4", "1709", "Synthetic Region 4"),
        ("WBDHU2", "HUC2", "17", "Pacific Northwest (synthetic)"),
    ):
        geopandas.GeoDataFrame(
            {code_col: [code], "Name": [label], "States": ["OR"]},
            geometry=[full],
            crs="EPSG:4326",
        ).to_file(out, layer=layer, driver="GPKG")

    return out


def _make_reach(
    session, *, name: str, lat: float | None, lon: float | None, huc: str | None = None
) -> Reach:
    reach = Reach(
        name=name,
        display_name=name,
        sort_name=name,
        latitude_start=lat,
        longitude_start=lon,
        huc=huc,
    )
    session.add(reach)
    session.flush()
    return reach


def test_load_huc12_returns_aligned_tree_and_codes(synthetic_gpkg):
    tree, codes = load_huc12(synthetic_gpkg)
    assert len(codes) == 3
    assert set(codes) == {"170912340101", "170912340102", "170912340201"}
    # Tree query for a point inside the first polygon should hit the first code.
    huc = assign_one(tree, codes, lat=0.5, lon=0.5)
    assert huc == "170912340101"


def test_assign_one_outside_returns_none(synthetic_gpkg):
    tree, codes = load_huc12(synthetic_gpkg)
    assert assign_one(tree, codes, lat=10.0, lon=10.0) is None


def test_assign_one_dispatches_to_each_polygon(synthetic_gpkg):
    tree, codes = load_huc12(synthetic_gpkg)
    # Polygons span [0,1)x[0,1), [1,2)x[0,1), [2,3)x[0,1) (x=lon, y=lat).
    assert assign_one(tree, codes, lat=0.5, lon=0.5) == "170912340101"
    assert assign_one(tree, codes, lat=0.5, lon=1.5) == "170912340102"
    assert assign_one(tree, codes, lat=0.5, lon=2.5) == "170912340201"


def test_run_assigns_inside_skips_outside_and_no_coords(session, synthetic_gpkg):
    inside = _make_reach(session, name="inside", lat=0.5, lon=0.5)
    outside = _make_reach(session, name="outside", lat=50.0, lon=50.0)
    no_coords = _make_reach(session, name="no_coords", lat=None, lon=None)
    session.commit()

    with (
        patch("kayak.huc.assign.get_session", return_value=session),
        patch.object(session, "close", lambda: None),
        patch.object(session, "commit", lambda: None),
    ):
        counts = run(gpkg=synthetic_gpkg)

    session.refresh(inside)
    session.refresh(outside)
    session.refresh(no_coords)

    assert inside.huc == "170912340101"
    assert outside.huc is None
    assert no_coords.huc is None
    assert counts["assigned"] == 1
    assert counts["outside_coverage"] == 1
    # iter_reaches_with_putin filters out NULL coords, so no_coords never enters
    # the loop. The counter only fires for reaches that pass the filter but
    # were re-NULLed before the call — that's fine for the single-id path.
    assert "no_coords" not in counts


def test_run_overwrites_existing_huc4(session, synthetic_gpkg):
    """A reach with a stale 4-char HUC4 gets the full 12-char code."""
    reach = _make_reach(session, name="hadold", lat=0.5, lon=0.5, huc="1709")
    session.commit()

    with (
        patch("kayak.huc.assign.get_session", return_value=session),
        patch.object(session, "close", lambda: None),
        patch.object(session, "commit", lambda: None),
    ):
        counts = run(gpkg=synthetic_gpkg)

    session.refresh(reach)
    assert reach.huc == "170912340101"
    assert counts["assigned"] == 1


def test_run_unchanged_when_huc_and_basin_match(session, synthetic_gpkg):
    """Re-running on a reach whose huc + basin already match counts as unchanged."""
    # synthetic HUC8 17091234 has name "Synthetic Basin" — pre-seed both columns
    # so the per-reach loop has nothing to write.
    reach = Reach(
        name="already",
        display_name="already",
        sort_name="already",
        latitude_start=0.5,
        longitude_start=0.5,
        huc="170912340101",
        basin="Synthetic Basin",
    )
    session.add(reach)
    session.commit()

    with (
        patch("kayak.huc.assign.get_session", return_value=session),
        patch.object(session, "close", lambda: None),
        patch.object(session, "commit", lambda: None),
    ):
        counts = run(gpkg=synthetic_gpkg)

    session.refresh(reach)
    assert reach.huc == "170912340101"
    assert reach.basin == "Synthetic Basin"
    assert counts["unchanged"] == 1
    assert counts.get("assigned", 0) == 0


def test_run_writes_basin_from_huc8_name(session, synthetic_gpkg):
    """A reach whose huc matches but basin is stale gets just the basin updated."""
    reach = Reach(
        name="basin_only",
        display_name="basin_only",
        sort_name="basin_only",
        latitude_start=0.5,
        longitude_start=0.5,
        huc="170912340101",
        basin="StaleCuratorTag",
    )
    session.add(reach)
    session.commit()

    with (
        patch("kayak.huc.assign.get_session", return_value=session),
        patch.object(session, "close", lambda: None),
        patch.object(session, "commit", lambda: None),
    ):
        counts = run(gpkg=synthetic_gpkg)

    session.refresh(reach)
    assert reach.huc == "170912340101"
    assert reach.basin == "Synthetic Basin"  # HUC8 17091234 name
    assert counts["assigned"] == 1
    assert counts.get("huc_changed", 0) == 0
    assert counts["basin_changed"] == 1


def test_dry_run_does_not_write(session, synthetic_gpkg):
    reach = _make_reach(session, name="dry", lat=0.5, lon=0.5, huc=None)
    session.commit()

    with (
        patch("kayak.huc.assign.get_session", return_value=session),
        patch.object(session, "close", lambda: None),
        patch.object(session, "commit", lambda: None),
    ):
        counts = run(gpkg=synthetic_gpkg, dry_run=True)

    session.refresh(reach)
    assert reach.huc is None  # no write under dry_run
    assert counts["assigned"] == 1


def test_upsert_huc_names_loads_only_read_levels(session, synthetic_gpkg):
    # R6.2: only HUC6 + HUC8 names are read anywhere, so upsert_huc_names carries
    # just those two levels; HUC2/4/10/12 and the old HUC2 fallback are dropped.
    rows = upsert_huc_names(session, synthetic_gpkg)
    session.commit()
    assert rows == 1 + 1  # one HUC6 + one HUC8 row in the fixture

    huc6 = session.get(HucName, "170912")
    huc8 = session.get(HucName, "17091234")
    assert (huc6.level, huc6.name) == (6, "Synthetic Region 6")
    assert (huc8.level, huc8.name) == (8, "Synthetic Basin")

    # The unread levels (and the former HUC2 fallback) are no longer loaded.
    assert session.get(HucName, "17") is None  # HUC2 (+ fallback)
    assert session.get(HucName, "1709") is None  # HUC4
    assert session.get(HucName, "1709123401") is None  # HUC10
    assert session.get(HucName, "170912340101") is None  # HUC12


def test_get_reach_huc_counts_buckets_by_length(session):
    _make_reach(session, name="r12", lat=1.0, lon=1.0, huc="170912340101")
    _make_reach(session, name="r4", lat=1.0, lon=1.0, huc="1709")
    _make_reach(session, name="rnull", lat=1.0, lon=1.0, huc=None)
    session.commit()

    counts = get_reach_huc_counts(session)
    assert counts.get(12) == 1
    assert counts.get(4) == 1
    assert counts.get(0) == 1
