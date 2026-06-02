"""Unit tests for kayak.tracing.osm (OSM main-channel graph-walk + gate).

The pure-Python helpers (name matching, the NHD gate) run everywhere; the
geometry/noding/graph-walk tests need shapely and are skipped without it.
"""

from __future__ import annotations

import pytest

from kayak.tracing import osm


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #
def test_name_matches_lenient_but_distinctive():
    assert osm.name_matches("McKenzie River", "McKenzie River")
    assert osm.name_matches("McKenzie River", "McKenzie")  # our river strings vary
    assert osm.name_matches("North Fork Alsea River", "Alsea, N. Fork")
    assert not osm.name_matches("Salmon Creek", "Hood River")
    assert not osm.name_matches(None, "McKenzie River")
    assert not osm.name_matches("River", "Creek")  # only generic tokens → no match


def test_gate_accepts_close_trace():
    line = [(44.0, -122.0), (44.0, -122.01), (44.0, -122.02)]
    assert osm.gate_ok(line, line)
    assert osm.max_deviation(line, line) == pytest.approx(0, abs=1e-6)


def test_gate_rejects_large_lateral_offset():
    nhd = [(44.0, -122.0), (44.0, -122.01), (44.0, -122.02)]
    osm_far = [(44.02, -122.0), (44.02, -122.01), (44.02, -122.02)]  # ~2.2 km north
    assert osm.max_deviation(osm_far, nhd) > osm.GATE_MAX_DEV_M
    assert not osm.gate_ok(osm_far, nhd)


def test_bbox_pad_grows_with_endpoint_separation():
    """The read window pads by a floor for short reaches but scales up with the
    endpoint separation so a long, laterally-bowing reach isn't clipped."""
    # short reach: pad stays at the 0.05 floor.  bbox = (minlon, minlat, maxlon, maxlat)
    b = osm._bbox((44.0, -122.00), (44.0, -122.01))
    assert b[2] - (-122.0) == pytest.approx(0.05) and -122.01 - b[0] == pytest.approx(0.05)
    # long reach (0.5 deg lon span): pad grows to 0.25 * 0.5 = 0.125
    b = osm._bbox((44.0, -122.0), (44.0, -122.5))
    assert b[2] - (-122.0) == pytest.approx(0.125)


def test_gate_rejects_length_mismatch():
    nhd = [(44.0, -122.0), (44.0, -122.05)]  # ~4 km
    osm_short = [(44.0, -122.0), (44.0, -122.005)]  # ~10% of it
    assert not osm.gate_ok(osm_short, nhd)  # length ratio well below 0.7


def test_gate_rejects_partial_trace_via_symmetry():
    """A partial OSM trace whose every vertex lies on the (longer) committed line
    passes the one-directional check, but the committed tail is far from OSM. The
    symmetric (Hausdorff) gate must reject it -- this is the Grande-Ronde-#232 class
    that slipped through a one-directional gate at scale."""
    committed = [
        (44.0, -122.00),
        (44.0, -122.01),
        (44.0, -122.02),
        (44.0, -122.03),
        (44.0, -122.04),
    ]
    osm_partial = committed[:4]  # covers ~75% (length ratio in range), misses the tail
    assert osm.max_deviation(osm_partial, committed) < 1.0  # forward dev ~0 (lies on committed)
    assert osm.max_deviation(committed, osm_partial) > osm.GATE_MAX_DEV_M  # tail is far
    assert osm.hausdorff(osm_partial, committed) > osm.GATE_MAX_DEV_M
    assert not osm.gate_ok(osm_partial, committed)


# --------------------------------------------------------------------------- #
# graph walk (needs shapely)
# --------------------------------------------------------------------------- #
shapely = pytest.importorskip("shapely")
from shapely.geometry import LineString  # noqa: E402


def _near(a, b, tol=0.0005):
    return abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol


def test_single_segment_is_clipped():
    """One way spanning both ends → trace is the clipped sub-line, put-in→take-out."""
    way = ("Test River", "river", LineString([(-122.00, 44.0), (-122.05, 44.0)]))
    putin, takeout = (44.0, -122.01), (44.0, -122.04)
    tr = osm.osm_trace_reach(putin, takeout, [way], river="Test River")
    assert tr is not None
    assert _near(tr[0], putin) and _near(tr[-1], takeout)


def test_two_segment_endpoint_chain():
    """Two ways sharing an endpoint → walk stitches put-in (way A) to take-out (way B)."""
    a = ("R", "river", LineString([(-122.00, 44.0), (-122.02, 44.0)]))
    b = ("R", "river", LineString([(-122.02, 44.0), (-122.04, 44.0)]))
    tr = osm.osm_trace_reach((44.0, -122.005), (44.0, -122.035), [a, b], river="R")
    assert tr is not None
    assert _near(tr[0], (44.0, -122.005)) and _near(tr[-1], (44.0, -122.035))


def test_noding_connects_midway_junction():
    """A tributary whose endpoint lands on the main channel's *interior* is
    disconnected in an endpoint-only graph; noding (unary_union) splits the main
    channel there so the walk connects. This is the 84%-of-no-path fix."""
    main = ("R", "river", LineString([(-122.00, 44.0), (-122.04, 44.0)]))
    trib = ("R", "river", LineString([(-122.02, 44.0), (-122.02, 43.98)]))  # joins main mid-line
    putin = (43.98, -122.02)  # bottom of the tributary
    takeout = (44.0, -122.038)  # west end of the main channel
    tr = osm.osm_trace_reach(putin, takeout, [main, trib], river="R")
    assert tr is not None  # only possible once `main` is noded at (-122.02, 44.0)
    assert _near(tr[0], putin) and _near(tr[-1], takeout)


def test_no_path_when_disconnected():
    """Two unconnected channels far apart → no path (→ caller falls back to NHD)."""
    a = ("R", "river", LineString([(-122.00, 44.0), (-122.02, 44.0)]))
    b = ("R", "river", LineString([(-122.50, 44.5), (-122.52, 44.5)]))  # nowhere near a
    tr = osm.osm_trace_reach((44.0, -122.005), (44.5, -122.515), [a, b], river="R")
    assert tr is None


def test_degenerate_clip_returns_none():
    """Put-in == take-out on a single segment → substring(t, t) is a Point → return
    None, not a malformed 1-vertex 'trace'."""
    way = ("R", "river", LineString([(-122.00, 44.0), (-122.05, 44.0)]))
    pt = (44.0, -122.02)
    assert osm.osm_trace_reach(pt, pt, [way], river="R") is None


def test_named_pool_falls_back_to_all_ways():
    """If the named-river pool snaps put-in/take-out onto disconnected named stubs,
    the all-ways fallback recovers the path via the (unnamed) real channel."""
    stub_a = ("R", "river", LineString([(-122.000, 44.01), (-122.005, 44.01)]))  # N of put-in
    stub_b = ("R", "river", LineString([(-122.050, 44.01), (-122.055, 44.01)]))  # N of take-out
    main = (None, "stream", LineString([(-122.00, 44.0), (-122.055, 44.0)]))  # the real channel
    putin, takeout = (44.0, -122.005), (44.0, -122.05)
    tr = osm.osm_trace_reach(putin, takeout, [stub_a, stub_b, main], river="R")
    assert tr is not None  # named pool (stubs) is disconnected; falls back to `main`
    assert _near(tr[0], putin) and _near(tr[-1], takeout)
