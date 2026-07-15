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
    _FORK_ORDER,
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

    def test_named_fork_peels_to_parent_basin(self):
        """A fork named for something other than a direction still peels."""
        assert basin_and_fork("Yankee Fork Salmon") == ("Salmon", "yankee")

    @pytest.mark.parametrize(
        "river",
        [
            # A named fork peels only with an explicit "Fork" after it —
            # otherwise "Yankee" would eat the first word of any river.
            "Yankee Creek",
            "Yankee Boy Creek",
            "Yankee",
        ],
    )
    def test_named_fork_requires_the_word_fork(self, river):
        assert basin_and_fork(river) == (river, "")

    def test_direction_still_peels_without_the_word_fork(self):
        """The relaxation stays for directions: "North Umpqua" IS a fork."""
        assert basin_and_fork("North Umpqua") == ("Umpqua", "north")

    @pytest.mark.parametrize(
        "river",
        [
            # A general "{Word} Fork {Basin}" rule would reclassify this; the
            # allowlist deliberately does not.
            "Coast Fork Willamette",
            # No basin follows "Fork", so there is nothing to peel onto.
            "Oak Grove Fork",
            "Clark Fork",
        ],
    )
    def test_other_fork_names_are_untouched(self, river):
        assert basin_and_fork(river) == (river, "")

    def test_empty(self):
        assert basin_and_fork("") == ("", "")


class TestCuratedForkOrder:
    def test_fork_order_matches_the_clubs_own_reach_curation(self):
        """_FORK_ORDER must agree with an authority outside this module.

        kayak_data's reach.csv already curates this basin, via `sort_name`
        "Salmon ag NN": ag 01 Yankee Fork, ag 02 EF Salmon, ag 03 MF,
        ag 04 EF of SF, ag 06-08 SF, ag 09-10 Little. Restricted to the
        forks that carry a gauge, that is the tuple below.

        Asserted against that citation rather than against a list mirroring
        the constant, which would pass for whatever order happened to be
        shipped. If this fails, re-derive it from reach.csv — do not simply
        paste in the new value.
        """
        assert _FORK_ORDER["salmon"] == ("yankee", "middle", "east", "south", "little")

    def test_yankee_fork_precedes_middle_fork_salmon(self):
        """The user-visible requirement, stated independently of the prefixes."""
        yankee = build_sort_name("Yankee Fork Salmon", 5950.0, 189.0)
        mf = build_sort_name("Middle Fork Salmon", 4384.4, 1042.0)
        assert yankee < mf

    def test_efsf_gauge_sorts_below_middle_fork_despite_being_higher(self):
        """Elevation must not drive fork order.

        EFSF's gauge is the basin's highest (6466 ft), but it is a fork of
        the South Fork and joins the mainstem downstream of the MF. Ranking
        forks by gauge elevation put it first; the club's curation puts it
        fourth. This is the case that caught that error.
        """
        efsf = build_sort_name("East Fork South Fork Salmon", 6466.0, 19.3)
        mf = build_sort_name("Middle Fork Salmon", 4384.4, 1042.0)
        assert efsf > mf, "EFSF must not lead the basin on gauge elevation"
        assert efsf < build_sort_name("South Fork Salmon", 3750.0, 330.0)

    def test_curated_forks_still_precede_the_mainstem(self):
        assert build_sort_name("Little Salmon", 1755.28, 576.0) < build_sort_name(
            "Salmon", 5900.0, 807.0
        )

    def test_other_basins_stay_alphabetical(self):
        """_FORK_ORDER names only the Salmon basin; nothing else is re-ranked."""
        assert build_sort_name("North Santiam", 655.0, 654.0) == "santiam|0north|009345|000654"
        assert build_sort_name("Middle Fork Willamette", 600.0, 100.0).startswith(
            "willamette|0middle|"
        )

    def test_unlisted_fork_in_curated_basin_falls_back_to_its_label(self):
        """It keeps a bare label and lands after the ranked forks, before mainstem."""
        west = build_sort_name("West Fork Salmon", 5000.0, 50.0)
        assert west.startswith("salmon|0west|")
        assert west > build_sort_name("Little Salmon", 1755.28, 576.0)  # after ranked forks
        assert west < build_sort_name("Salmon", 5900.0, 807.0)  # before mainstem


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
