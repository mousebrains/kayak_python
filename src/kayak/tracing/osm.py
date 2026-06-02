"""OSM main-channel tracing -- shortest-path walk over noded waterway linework.

NHD HR represents wide/braided rivers as ``FType=558`` ArtificialPath centerlines
that, at islands/braids, take a different channel than the paddled one (off up to
~290 m on the McKenzie). OSM's named ``waterway`` channel matches the paddled line
(~5-8 m vs a hand-surveyed KML on the McKenzie; ~8 m median agreement with NHD
across 344 reaches). This module walks a put-in->take-out shortest path over the
**noded** OSM waterway graph -- the linework is split at every intersection
(``shapely.ops.unary_union``) so a tributary joining the main channel *mid-way*
becomes a graph node (without that, ~84% of "no path" failures occur).

:func:`trace_reach` is the orchestrator: it runs the OSM walk *and* the NHD trace,
and keeps OSM only when it passes a gate against NHD (length ratio in
``GATE_LEN_RATIO`` **and** symmetric/Hausdorff max deviation <= ``GATE_MAX_DEV_M``,
i.e. close in *both* directions) -- otherwise it falls back to the NHD trace. So it
is never worse than NHD: braided reaches improve, wrong-fork / partial /
coverage-gap OSM results are rejected.

Heavy imports (shapely, osgeo) are deferred into the functions so importing the
package stays cheap and works where they're absent. See ``docs/tracing.md``.
"""

from __future__ import annotations

import heapq
import itertools
import math
import re
from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any

# A graph node is either a (lon, lat) coordinate or a virtual "SRC"/"DST" tag.
Coord = tuple[float, float]
Node = tuple[float, float] | str
Way = tuple[Any, Any, Any]  # (name: str|None, waterway: str|None, shapely LineString)
Edge = tuple[Node, float, list[Coord]]  # (neighbour, weight, coords oriented u->v)

# Gate thresholds (OSM accepted only if it agrees with the NHD trace this well).
GATE_LEN_RATIO = (0.7, 1.4)
GATE_MAX_DEV_M = 500.0
DEFAULT_OSM_SOURCE = "Trace-cache/OSM/named_waterways.gpkg"
_RIVER = "river"
# Generic tokens stripped before fuzzy river-name matching.
_GENERIC = {
    "river", "creek", "fork", "north", "south", "east", "west", "middle", "the", "of",
    "n", "s", "e", "w", "mf", "nf", "sf", "ef", "ck", "cr", "br", "branch", "rv",
}  # fmt: skip


def _hav(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _len_lonlat(c: Sequence[Coord]) -> float:
    return sum(_hav(c[i][1], c[i][0], c[i + 1][1], c[i + 1][0]) for i in range(len(c) - 1))


def _len_latlon(c: Sequence[Coord]) -> float:
    return sum(_hav(c[i][0], c[i][1], c[i + 1][0], c[i + 1][1]) for i in range(len(c) - 1))


def _core(name: str | None) -> set[str]:
    if not name:
        return set()
    toks = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    return {t for t in toks if t not in _GENERIC and len(t) > 1}


def name_matches(osm_name: str | None, river: str | None) -> bool:
    """Lenient match: share a distinctive (non-generic) token, e.g. 'McKenzie'."""
    return bool(_core(osm_name) & _core(river))


# --------------------------------------------------------------------------- #
# data access
# --------------------------------------------------------------------------- #
def read_waterways(source: str, bbox: tuple[float, float, float, float]) -> list[Way]:
    """Read ``(name, waterway, shapely.LineString)`` from an OSM waterway GPKG
    within ``bbox`` = (min_lon, min_lat, max_lon, max_lat). Requires osgeo+shapely."""
    from osgeo import ogr
    from shapely import wkb

    ds = ogr.Open(source)
    if ds is None:
        raise FileNotFoundError(f"cannot open OSM source: {source}")
    layer = ds.GetLayer(0)
    layer.SetSpatialFilterRect(*bbox)
    defn = layer.GetLayerDefn()
    fields = {defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())}
    ways: list[Way] = []
    for feat in layer:
        g = feat.GetGeometryRef()
        if g is None:
            continue
        geom = wkb.loads(bytes(g.ExportToWkb()))
        name = feat.GetField("name") if "name" in fields else None
        ww = feat.GetField("waterway") if "waterway" in fields else None
        parts = geom.geoms if geom.geom_type == "MultiLineString" else [geom]
        ways.extend((name, ww, p) for p in parts if len(p.coords) >= 2)
    ds = None
    return ways


def _node_ways(ways: list[Way]) -> list[Way]:
    """Split the linework at every intersection (planar noding) so mid-way
    junctions become shared endpoints; reattach each segment's source name/type."""
    from shapely.ops import unary_union

    if not ways:
        return []
    noded = unary_union([w[2] for w in ways])
    segs = list(noded.geoms) if noded.geom_type == "MultiLineString" else [noded]
    out: list[Way] = []
    for seg in segs:
        if len(seg.coords) < 2:
            continue
        mid = seg.interpolate(0.5, normalized=True)
        # Attribute the segment to the nearest original way (it lies on its parent,
        # so distance ~0). On a tie -- overlapping collinear OSM ways on the same
        # centerline -- prefer a 'river'-typed parent so the shared stretch keeps
        # the main-channel penalty rather than a coincident stream's.
        name, ww, _ = min(ways, key=lambda w: (w[2].distance(mid), w[1] != _RIVER))
        out.append((name, ww, seg))
    return out


# --------------------------------------------------------------------------- #
# graph walk
# --------------------------------------------------------------------------- #
def _penalty(name: str | None, ww: str | None, river: str | None) -> float:
    if ww == _RIVER and (river is None or name_matches(name, river)):
        return 1.0
    if ww == _RIVER:
        return 3.0
    return 12.0


def _dijkstra(adj: dict[Node, list[Edge]], src: Node, dst: Node) -> list[Coord] | None:
    """Shortest path; returns the concatenated (lon, lat) coords or None.

    ``adj[u]`` = list of ``(v, weight, coords_u_to_v)`` where ``coords_u_to_v`` is
    already oriented from u to v.
    """
    # A monotonic counter breaks distance ties so heapq never compares the Node
    # itself — nodes are coord tuples *and* "SRC"/"DST" strings, and tuple<str
    # raises TypeError.
    counter = itertools.count()
    dist: dict[Node, float] = {src: 0.0}
    prev: dict[Node, tuple[Node, list[Coord]]] = {}
    pq: list[tuple[float, int, Node]] = [(0.0, next(counter), src)]
    seen: set[Node] = set()
    while pq:
        d, _, u = heapq.heappop(pq)
        if u in seen:
            continue
        seen.add(u)
        if u == dst:
            break
        for v, w, coords in adj.get(u, ()):
            nd = d + w
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                prev[v] = (u, coords)
                heapq.heappush(pq, (nd, next(counter), v))
    if dst not in dist:
        return None
    chain: list[list[Coord]] = []
    node: Node = dst
    while node != src:
        pu, coords = prev[node]
        chain.append(coords)
        node = pu
    chain.reverse()
    out: list[Coord] = []
    for s in chain:
        out.extend(s[1:] if out and out[-1] == s[0] else s)
    return out


def _round_coords(line: Any) -> list[Coord]:
    """A shapely line's coords snapped to the graph-node grid (~0.1 m). The 6-dp
    rounding is load-bearing: it's what makes shared endpoints compare equal so the
    Dijkstra adjacency connects."""
    return [(round(x, 6), round(y, 6)) for x, y in line.coords]


def _walk(
    segs: list[Way], pp: Any, tp: Any, pool: list[int], river: str | None
) -> list[Coord] | None:
    """Snap put-in/take-out (shapely Points ``pp``/``tp``) to the nearest segment in
    ``pool``, then shortest-path over the noded graph. ``pool`` only restricts which
    segments the endpoints *snap* to; the traversable graph is built over **all**
    ``segs`` (connectivity must not depend on the pool). Returns (lon, lat) coords or
    None (no connected path, or a degenerate clip)."""
    from shapely.ops import substring

    def nearest(pt: Any) -> tuple[int, float]:
        i = min(pool, key=lambda j: segs[j][2].distance(pt))
        return i, segs[i][2].project(pt)

    wp, pi_proj = nearest(pp)
    wt, to_proj = nearest(tp)

    if wp == wt:  # one segment spans both ends -- just clip it
        line = segs[wp][2]
        a, b = sorted((pi_proj, to_proj))
        coords = _round_coords(substring(line, a, b))
        return coords if len(coords) >= 2 else None  # substring(t, t) is a Point

    adj: dict[Node, list[Edge]] = defaultdict(list)
    for name, ww, line in segs:
        c = _round_coords(line)
        w = _len_lonlat(c) * _penalty(name, ww, river)
        adj[c[0]].append((c[-1], w, c))
        adj[c[-1]].append((c[0], w, list(reversed(c))))
    # virtual SRC/DST spliced onto their segment at the projection point
    for tag, seg_i, proj, to_src in (("SRC", wp, pi_proj, True), ("DST", wt, to_proj, False)):
        line = segs[seg_i][2]
        c = _round_coords(line)
        head = _round_coords(substring(line, 0, proj))
        tail = _round_coords(substring(line, proj, line.length))
        if to_src:  # SRC -> each endpoint (coords run proj -> endpoint)
            adj[tag].append((c[0], _len_lonlat(head), list(reversed(head))))
            adj[tag].append((c[-1], _len_lonlat(tail), tail))
        else:  # each endpoint -> DST (coords run endpoint -> proj)
            adj[c[0]].append((tag, _len_lonlat(head), head))
            adj[c[-1]].append((tag, _len_lonlat(tail), list(reversed(tail))))
    return _dijkstra(adj, "SRC", "DST")


def osm_trace_reach(
    putin: Coord, takeout: Coord, ways: list[Way], river: str | None = None
) -> list[Coord] | None:
    """Trace put-in->take-out along the OSM main channel.

    ``putin``/``takeout`` are (lat, lon). Returns (lat, lon) vertices, or None if
    the noded graph has no connected path (OSM coverage gap)."""
    from shapely.geometry import Point

    segs = _node_ways(ways)
    if not segs:
        return None
    pp, tp = Point(putin[1], putin[0]), Point(takeout[1], takeout[0])

    # Snap to the named-river pool first (geometry still anchors it). If that yields
    # no path -- the matched name is on disconnected pieces, or the put-in's real
    # segment was mis-named by noding -- retry over all waterways before giving up.
    named = [i for i, (n, w, _) in enumerate(segs) if w == _RIVER and name_matches(n, river)]
    coords = _walk(segs, pp, tp, named, river) if named else None
    if coords is None:
        coords = _walk(segs, pp, tp, list(range(len(segs))), river)
    if coords is None:
        return None

    latlon: list[Coord] = [(la, lo) for lo, la in coords]
    # Orient put-in -> take-out by the assignment that puts the start nearest the
    # put-in AND the end nearest the take-out (robust on oxbow/horseshoe reaches
    # where one anchor alone is ambiguous).
    fwd = _hav(*latlon[0], *putin) + _hav(*latlon[-1], *takeout)
    rev = _hav(*latlon[-1], *putin) + _hav(*latlon[0], *takeout)
    if rev < fwd:
        latlon.reverse()
    return latlon if len(latlon) >= 2 else None


# --------------------------------------------------------------------------- #
# gate + orchestration
# --------------------------------------------------------------------------- #
def _seg_d(p: Coord, a: Coord, b: Coord) -> float:
    mlat = math.radians(p[0])
    kx, ky = 111320 * math.cos(mlat), 110540
    px, py, ax, ay, bx, by = p[1] * kx, p[0] * ky, a[1] * kx, a[0] * ky, b[1] * kx, b[0] * ky
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def max_deviation(a: Sequence[Coord], b: Sequence[Coord]) -> float:
    """Max distance (m) of polyline ``a``'s vertices to polyline ``b`` (both lat,lon).

    Returns ``inf`` for a degenerate input (so a gate treats it as far/unusable)."""
    if not a or len(b) < 2:
        return math.inf
    return max(min(_seg_d(p, b[i], b[i + 1]) for i in range(len(b) - 1)) for p in a)


def hausdorff(a: Sequence[Coord], b: Sequence[Coord]) -> float:
    """Symmetric max separation (m) -- ``max`` of both one-directional deviations.

    The one-directional ``max_deviation(osm, nhd)`` only checks that every OSM
    vertex is near the NHD line; a *partial* OSM trace whose points all lie on a
    long, winding NHD line passes it even though OSM skipped a whole section. The
    reverse direction (NHD vertices to OSM) catches that, so the gate uses both.
    """
    return max(max_deviation(a, b), max_deviation(b, a))


def gate_ok(osm_coords: Sequence[Coord], nhd_coords: Sequence[Coord]) -> bool:
    """True iff the OSM trace agrees with NHD closely enough to trust it."""
    if len(osm_coords) < 2 or len(nhd_coords) < 2:
        return False
    lo, ln = _len_latlon(osm_coords), _len_latlon(nhd_coords)
    if ln <= 0:
        return False
    ratio = lo / ln
    if not (GATE_LEN_RATIO[0] <= ratio <= GATE_LEN_RATIO[1]):
        return False
    return hausdorff(osm_coords, nhd_coords) <= GATE_MAX_DEV_M


def _bbox(putin: Coord, takeout: Coord, pad: float = 0.05) -> tuple[float, float, float, float]:
    """Lon/lat bbox around the endpoints (the OSM read-window *fallback*, used only
    when there's no NHD trace to bound by). ``pad`` is a floor that grows with the
    endpoint separation so a long reach bowing outside the put-in/take-out box
    still has its channel inside the window. (When the NHD trace exists,
    :func:`_coords_bbox` of it is used instead -- robust to *any* shape, incl. a
    tight oxbow whose endpoints are close, which this endpoint box can't cover.)"""
    sep = max(abs(putin[0] - takeout[0]), abs(putin[1] - takeout[1]))
    pad = max(pad, 0.25 * sep)
    return (
        min(putin[1], takeout[1]) - pad, min(putin[0], takeout[0]) - pad,
        max(putin[1], takeout[1]) + pad, max(putin[0], takeout[0]) + pad,
    )  # fmt: skip


def _coords_bbox(coords: Sequence[Coord], pad: float) -> tuple[float, float, float, float]:
    """Lon/lat bbox enclosing a (lat, lon) polyline, padded by ``pad`` degrees."""
    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    return (min(lons) - pad, min(lats) - pad, max(lons) + pad, max(lats) + pad)


def trace_reach(
    putin: Coord,
    takeout: Coord,
    river: str | None = None,
    osm_source: str = DEFAULT_OSM_SOURCE,
    huc4: str | None = None,
    verbose: bool = False,
) -> tuple[list[Coord] | None, str]:
    """Trace a reach, preferring OSM but falling back to NHD via the gate.

    Returns ``(coords, source)`` where ``coords`` is [(lat, lon), ...] and
    ``source`` is ``"osm"`` (gated against NHD), ``"osm (ungated)"`` (NHD was
    unavailable, so OSM couldn't be cross-checked — eyeball it), or ``"nhd"``.
    ``putin``/``takeout`` are (lat, lon)."""
    from . import trace as nhd

    log: Callable[..., None] = print if verbose else (lambda *a, **k: None)
    nhd_coords: list[Coord] | None
    try:
        nhd_coords = nhd.trace_reach(putin, takeout, huc4=huc4, verbose=False)
    except Exception as exc:  # fall back to OSM if the NHD trace blows up
        nhd_coords = None
        log(f"NHD trace failed: {exc}")

    osm_coords: list[Coord] | None = None
    try:
        # Read OSM within the NHD trace's bbox when we have one: it bounds the
        # actual channel for *any* reach shape (incl. a tight oxbow whose endpoints
        # are close), so the OSM main channel can't be clipped out of the window.
        # Fall back to the endpoint bbox only when there's no NHD trace.
        window = _coords_bbox(nhd_coords, 0.02) if nhd_coords else _bbox(putin, takeout)
        ways = read_waterways(osm_source, window)
        osm_coords = osm_trace_reach(putin, takeout, ways, river)
    except Exception as exc:  # OSM is best-effort; NHD is the floor
        log(f"OSM trace unavailable: {exc}")

    if osm_coords:
        if nhd_coords is None:
            log("WARNING: NHD trace unavailable -- using UNGATED OSM (no cross-check; verify it)")
            return osm_coords, "osm (ungated)"
        if gate_ok(osm_coords, nhd_coords):
            log("using OSM (passed gate vs NHD)")
            return osm_coords, "osm"
        log("OSM rejected by gate (diverges from NHD) -- using NHD")
    return nhd_coords, "nhd"
