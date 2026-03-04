"""Tests for kayak.utils.simplify — Douglas-Peucker and geom parsing."""

from kayak.utils.simplify import parse_geom, simplify

# ---------------------------------------------------------------------------
# simplify()
# ---------------------------------------------------------------------------

class TestSimplify:
    def test_straight_line_reduces_to_endpoints(self):
        points = [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)]
        result = simplify(points, 0.1)
        assert result == [(0, 0), (4, 0)]

    def test_significant_bend_preserved(self):
        points = [(0, 0), (1, 0), (2, 5), (3, 0), (4, 0)]
        result = simplify(points, 0.1)
        assert (2, 5) in result
        assert result[0] == (0, 0)
        assert result[-1] == (4, 0)

    def test_two_points_unchanged(self):
        points = [(0, 0), (1, 1)]
        assert simplify(points, 0.1) == [(0, 0), (1, 1)]

    def test_one_point_unchanged(self):
        points = [(5, 5)]
        assert simplify(points, 0.1) == [(5, 5)]

    def test_empty_list(self):
        assert simplify([], 0.1) == []

    def test_large_epsilon_reduces_to_endpoints(self):
        points = [(0, 0), (1, 0.5), (2, 0.1), (3, -0.3), (4, 0)]
        result = simplify(points, 100)
        assert result == [(0, 0), (4, 0)]


# ---------------------------------------------------------------------------
# parse_geom()
# ---------------------------------------------------------------------------

class TestParseGeom:
    def test_standard_parsing(self):
        geom = "-122.5 45.1,-122.6 45.2,-122.7 45.3"
        result = parse_geom(geom)
        assert result == [(-122.5, 45.1), (-122.6, 45.2), (-122.7, 45.3)]

    def test_empty_string(self):
        assert parse_geom("") == []

    def test_none_returns_empty(self):
        assert parse_geom(None) == []

    def test_whitespace_only(self):
        assert parse_geom("   ") == []

    def test_invalid_entries_skipped(self):
        geom = "-122.5 45.1,bad data,-122.7 45.3"
        result = parse_geom(geom)
        assert result == [(-122.5, 45.1), (-122.7, 45.3)]

    def test_single_point(self):
        geom = "-122.5 45.1"
        result = parse_geom(geom)
        assert result == [(-122.5, 45.1)]
