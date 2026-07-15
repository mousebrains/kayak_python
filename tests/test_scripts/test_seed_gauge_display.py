"""Tests for the gauge display/sort-key helpers in ``seed_gauge_display.py``.

``sort_name`` is the whole row order of gauges.html (the page sorts
alphabetically on it), so the basin/fork split is load-bearing for what a
reader sees.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from seed_gauge_display import (
    basin_and_fork,
    build_display_name,
    build_sort_name,
)


class TestBasinAndFork:
    @pytest.mark.parametrize(
        ("river", "expected"),
        [
            # Mainstems — nothing to peel.
            ("Hood", ("Hood", "")),
            ("Deschutes", ("Deschutes", "")),
            # Directional forks, spelled both ways.
            ("North Fork Alsea", ("Alsea", "north")),
            ("North Umpqua", ("Umpqua", "north")),
            # Compound forks: the first modifier wins and keeps the row with
            # its basin's other tributaries.
            ("East Fork of South Fork Salmon", ("Salmon", "east")),
            ("North Fork of Middle Fork Willamette", ("Willamette", "north")),
        ],
    )
    def test_directional_forks(self, river, expected):
        assert basin_and_fork(river) == expected

    @pytest.mark.parametrize(
        ("river", "expected"),
        [
            ("Little Deschutes", ("Deschutes", "little")),
            ("Little Sandy", ("Sandy", "little")),
            ("Little Salmon", ("Salmon", "little")),
            # Both modifiers peel; "little" wins as the first seen, so this
            # sorts with the Santiam forks — ahead of North Santiam.
            ("Little North Santiam", ("Santiam", "little")),
        ],
    )
    def test_little_peels_for_known_tributaries(self, river, expected):
        """An allowlisted "Little X" groups with X instead of forming its own basin."""
        assert basin_and_fork(river) == expected

    @pytest.mark.parametrize(
        ("river", "expected"),
        [
            # Reaches the Columbia on its own, despite sharing HUC8 17070105
            # with the White Salmon — so it is not a fork of it.
            ("Little White Salmon", ("Little White Salmon", "")),
            # A river actually named "Little" — no parent to peel onto.
            ("Little", ("Little", "")),
            # Not adjudicated, so it keeps the status-quo basin.
            ("Little Nestucca", ("Little Nestucca", "")),
        ],
    )
    def test_little_stays_put_when_not_a_known_tributary(self, river, expected):
        """ "Little" is not a blanket modifier — independent rivers keep their basin."""
        assert basin_and_fork(river) == expected

    def test_empty(self):
        assert basin_and_fork("") == ("", "")


class TestBuildSortName:
    def test_little_deschutes_sorts_ahead_of_mainstem_deschutes(self):
        """The Little Deschutes must precede every mainstem Deschutes gauge.

        Gauge 26 (Deschutes at Wickiup) is the first mainstem row by
        elevation; fork_rank 0 puts the Little Deschutes ahead of it
        regardless of the fork's own elevation/DA.
        """
        little = build_sort_name("Little Deschutes", None, None)
        wickiup = build_sort_name("Deschutes", 4257.41, 483.0)
        assert little == "deschutes|0little|999999|999999"
        assert wickiup == "deschutes|9|005743|000483"
        assert little < wickiup

    def test_forks_precede_mainstem_in_same_basin(self):
        assert build_sort_name("Little Sandy", 720.0, 23.0) < build_sort_name("Sandy", 720.0, 23.0)

    def test_little_salmon_precedes_mainstem_salmon(self):
        assert build_sort_name("Little Salmon", 2000.0, 576.0) < build_sort_name(
            "Salmon", 2000.0, 576.0
        )

    def test_little_north_santiam_precedes_north_santiam(self):
        """ "little" sorts before "north" within the shared Santiam basin."""
        assert build_sort_name("Little North Santiam", 655.0, 112.0) < build_sort_name(
            "North Santiam", 655.0, 112.0
        )

    def test_little_white_salmon_keeps_its_own_basin(self):
        """Not a fork, so it neither joins nor jumps the White Salmon group."""
        assert build_sort_name("Little White Salmon", 925.0, None) == (
            "little white salmon|9|009075|999999"
        )

    def test_null_metadata_sorts_to_end_of_its_group(self):
        """NULL elevation/DA fall back to sentinels, not to the front."""
        assert build_sort_name("Deschutes", None, None) == "deschutes|9|999999|999999"
        assert build_sort_name("Deschutes", 4257.41, 483.0) < build_sort_name(
            "Deschutes", None, None
        )

    def test_elevation_descends_and_da_ascends(self):
        """Upstream (higher, smaller catchment) sorts first."""
        high = build_sort_name("Deschutes", 4257.41, 483.0)
        low = build_sort_name("Deschutes", 167.54, 10500.0)
        assert high < low


class TestBuildDisplayName:
    def test_river_at_location(self):
        assert build_display_name("Little Deschutes", "La Pine") == "Little Deschutes at La Pine"

    def test_river_only(self):
        assert build_display_name("Little Deschutes", "") == "Little Deschutes"
