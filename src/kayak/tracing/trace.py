#!/usr/bin/env python3
"""Trace a stream reach between put-in and take-out using NHD HR network data.

Uses the NHDPlusFlowlineVAA HydroSeq chain for gap-free downstream tracing.
Prefers pre-extracted GPKGs in Trace-cache/trace/ (fast spatial index).
Falls back to raw HUC4 GDB ZIPs in Trace-cache/NHD/hr/ (slow, full scan).

Pre-extract with: bash scripts/extract_trace_data.sh

Usage:
    levels trace --putin LAT,LON --takeout LAT,LON [options]

Examples:
    # Battle Creek, Idaho (60.5 mi per AW)
    levels trace \\
        --putin 42.694599,-116.400002 \\
        --takeout 42.237221,-116.523888 \\
        --name "Battle Creek"

    # Output CSV only (no map)
    levels trace \\
        --putin 42.694599,-116.400002 \\
        --takeout 42.237221,-116.523888 \\
        --csv-only

    # Specify HUC4 directly (skips auto-detection)
    levels trace \\
        --putin 42.694599,-116.400002 \\
        --takeout 42.237221,-116.523888 \\
        --huc4 1705
"""

import argparse
import contextlib
import math
import os

from osgeo import ogr

ogr.UseExceptions()

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
TRACE_CACHE = os.path.join(_REPO_ROOT, "Trace-cache")
TRACE_DIR = os.path.join(TRACE_CACHE, "trace")
NHD_HR_DIR = os.path.join(TRACE_CACHE, "NHD", "hr")


def haversine(lat1, lon1, lat2, lon2):
    """Distance in miles between two lat/lon points."""
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2 * R * math.asin(math.sqrt(a))


def _scan_dir_for_huc4(directory, accept_fn, path_fn, layer_name, huc4_from_name, bbox):
    """Scan ``directory`` for files matching ``accept_fn``; return the HUC4
    whose ``layer_name`` has a feature inside ``bbox``, or None.

    Shared body for find_huc4's two passes (pre-extracted GPKGs and raw
    GDB zips). ``path_fn(name)`` builds the OGR-openable path for a file
    in the dir; ``huc4_from_name(name)`` extracts the HUC4 code from the
    file name.
    """
    if not os.path.isdir(directory):
        return None
    minx, miny, maxx, maxy = bbox
    for name in sorted(os.listdir(directory)):
        if not accept_fn(name):
            continue
        ds = ogr.Open(path_fn(name))
        layer = ds.GetLayerByName(layer_name) if ds else None
        if layer is not None:
            layer.SetSpatialFilterRect(minx, miny, maxx, maxy)
            if layer.GetFeatureCount() > 0:
                ds = None
                return huc4_from_name(name)
        ds = None
    return None


def find_huc4(lat, lon, buffer_deg=0.15):
    """Find which HUC4 has flowlines near the given coordinates.

    Uses a spatial query rather than just bounding-box extent, so overlapping
    HUC4s are resolved by actual flowline proximity.
    Checks pre-extracted GPKGs first (fast), then raw GDB ZIPs.
    """
    bbox = (lon - buffer_deg, lat - buffer_deg, lon + buffer_deg, lat + buffer_deg)
    # Try pre-extracted GPKGs first.
    huc4 = _scan_dir_for_huc4(
        TRACE_DIR,
        accept_fn=lambda n: n.startswith("trace_") and n.endswith(".gpkg"),
        path_fn=lambda n: os.path.join(TRACE_DIR, n),
        layer_name="flowline",
        huc4_from_name=lambda n: n.replace("trace_", "").replace(".gpkg", ""),
        bbox=bbox,
    )
    if huc4 is not None:
        return huc4
    # Fall back to raw GDB ZIPs.
    return _scan_dir_for_huc4(
        NHD_HR_DIR,
        accept_fn=lambda n: n.endswith("_GDB.zip"),
        path_fn=lambda n: f"/vsizip/{NHD_HR_DIR}/{n}",
        layer_name="NHDFlowline",
        huc4_from_name=lambda n: n.split("_")[2],
        bbox=bbox,
    )


def data_source(huc4):
    """Return (path, layer_names) for a HUC4.

    Prefers pre-extracted GPKG (fast spatial index).
    Falls back to raw GDB ZIP (slow, full scan).

    Returns (path, flowline_layer, vaa_layer, vaa_fields).
    """
    gpkg = os.path.join(TRACE_DIR, f"trace_{huc4}.gpkg")
    if os.path.isfile(gpkg):
        return gpkg, "flowline", "vaa", ("NHDPlusID", "HydroSeq", "DnHydroSeq")

    gdb = f"/vsizip/{NHD_HR_DIR}/NHDPLUS_H_{huc4}_HU4_GDB.zip"
    return gdb, "NHDFlowline", "NHDPlusFlowlineVAA", ("NHDPlusID", "HydroSeq", "DnHydroSeq")


def load_vaa(src):
    """Load the full VAA HydroSeq network index.

    Args:
        src: (path, flowline_layer, vaa_layer, vaa_fields) from data_source()

    Returns:
        by_hydroseq: {HydroSeq: (NHDPlusID, DnHydroSeq)}
        by_nhdpid:   {NHDPlusID: (HydroSeq, DnHydroSeq)}
    """
    path, _, vaa_layer_name, _ = src
    ds = ogr.Open(path)
    layer = ds.GetLayerByName(vaa_layer_name)
    by_hydroseq = {}
    by_nhdpid = {}
    for feat in layer:
        nid = feat.GetField("NHDPlusID")
        hseq = feat.GetField("HydroSeq")
        dn = feat.GetField("DnHydroSeq")
        by_hydroseq[hseq] = (nid, dn)
        by_nhdpid[nid] = (hseq, dn)
    ds = None
    return by_hydroseq, by_nhdpid


def find_nearest_flowline(lat, lon, src, buffer_deg=0.15):
    """Find the NHDFlowline segment nearest to a point.

    Returns (NHDPlusID, GNIS_Name, distance_degrees).
    """
    path, fl_layer_name, _, _ = src
    ds = ogr.Open(path)
    layer = ds.GetLayerByName(fl_layer_name)
    layer.SetSpatialFilterRect(
        lon - buffer_deg, lat - buffer_deg, lon + buffer_deg, lat + buffer_deg
    )

    pt = ogr.Geometry(ogr.wkbPoint)
    pt.AddPoint(lon, lat)

    best_id = None
    best_name = None
    best_dist = float("inf")
    for feat in layer:
        geom = feat.GetGeometryRef()
        if geom is None:
            continue
        dist = pt.Distance(geom)
        if dist < best_dist:
            best_dist = dist
            best_id = feat.GetField("NHDPlusID")
            best_name = feat.GetField("GNIS_Name")
    ds = None
    return best_id, best_name, best_dist


def trace_hydroseq(start_id, end_id, by_hydroseq, by_nhdpid, max_steps=2000):
    """Follow the DnHydroSeq chain from start to end.

    Returns list of NHDPlusIDs along the path, or None if end not reached.
    """
    if start_id not in by_nhdpid:
        return None
    current_hseq = by_nhdpid[start_id][0]
    path = []
    for _ in range(max_steps):
        if current_hseq not in by_hydroseq:
            break
        nid, dn_hseq = by_hydroseq[current_hseq]
        path.append(nid)
        if nid == end_id:
            return path
        if dn_hseq == 0:
            break
        current_hseq = dn_hseq
    return None


def find_nearest_on_path(lat, lon, path, src):
    """Find the path segment closest to a point.

    When the exact end NHDPlusID isn't on the HydroSeq chain (e.g. the take-out
    is across the river from the nearest segment), we trace past it and trim.

    Returns the index into `path` of the nearest segment.
    """
    data_path, fl_layer_name, _, _ = src
    ds = ogr.Open(data_path)
    layer = ds.GetLayerByName(fl_layer_name)
    needed = set(path)
    geoms = {}
    for feat in layer:
        nid = feat.GetField("NHDPlusID")
        if nid in needed:
            geoms[nid] = feat.GetGeometryRef().Clone()
    ds = None

    pt = ogr.Geometry(ogr.wkbPoint)
    pt.AddPoint(lon, lat)
    best_idx = 0
    best_dist = float("inf")
    for i, nid in enumerate(path):
        if nid in geoms:
            d = pt.Distance(geoms[nid])
            if d < best_dist:
                best_dist = d
                best_idx = i
    return best_idx, geoms


def extract_coords(geom):
    """Extract (lat, lon) pairs from a geometry, linearizing curves."""
    coords = []
    linear = (
        geom.GetLinearGeometry()
        if geom.GetGeometryName() not in ("LINESTRING", "MULTILINESTRING")
        else geom
    )
    if linear.GetGeometryName() == "MULTILINESTRING":
        for i in range(linear.GetGeometryCount()):
            line = linear.GetGeometryRef(i)
            for j in range(line.GetPointCount()):
                x, y = line.GetPoint(j)[:2]
                coords.append((y, x))
    else:
        for j in range(linear.GetPointCount()):
            x, y = linear.GetPoint(j)[:2]
            coords.append((y, x))
    return coords


def build_trace(path, geoms, putin, takeout):
    """Assemble ordered coordinates from path segments.

    Handles segment reversal so each connects to the previous, and
    orients the full trace from put-in to take-out.
    """
    all_coords = []
    for nid in path:
        if nid not in geoms:
            continue
        seg = extract_coords(geoms[nid])
        if all_coords and seg:
            d_fwd = math.hypot(all_coords[-1][0] - seg[0][0], all_coords[-1][1] - seg[0][1])
            d_rev = math.hypot(all_coords[-1][0] - seg[-1][0], all_coords[-1][1] - seg[-1][1])
            if d_rev < d_fwd:
                seg = list(reversed(seg))
            if (
                abs(seg[0][0] - all_coords[-1][0]) < 1e-8
                and abs(seg[0][1] - all_coords[-1][1]) < 1e-8
            ):
                seg = seg[1:]
        all_coords.extend(seg)

    # Orient from put-in to take-out
    if all_coords:
        d_start = haversine(all_coords[0][0], all_coords[0][1], putin[0], putin[1])
        d_end = haversine(all_coords[-1][0], all_coords[-1][1], putin[0], putin[1])
        if d_end < d_start:
            all_coords = list(reversed(all_coords))

    # Trim to put-in and take-out — find closest points and clip
    if len(all_coords) > 2:
        pi_idx = min(
            range(len(all_coords)),
            key=lambda i: (all_coords[i][0] - putin[0]) ** 2 + (all_coords[i][1] - putin[1]) ** 2,
        )
        to_idx = min(
            range(len(all_coords)),
            key=lambda i: (
                (all_coords[i][0] - takeout[0]) ** 2 + (all_coords[i][1] - takeout[1]) ** 2
            ),
        )
        if pi_idx > to_idx:
            pi_idx, to_idx = to_idx, pi_idx
        all_coords = all_coords[pi_idx : to_idx + 1]

    return all_coords


def total_distance(coords):
    """Sum haversine distances along a coordinate list, in miles."""
    return sum(
        haversine(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1])
        for i in range(len(coords) - 1)
    )


def write_csv(coords, filename):
    with open(filename, "w") as f:
        f.write("latitude,longitude\n")
        for lat, lon in coords:
            f.write(f"{lat:.6f},{lon:.6f}\n")


def make_map(coords, putin, takeout, name, miles, filename):
    try:
        import contextily as cx
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  pip install contextily matplotlib for map generation")
        return

    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]

    _fig, ax = plt.subplots(1, 1, figsize=(10, 14))
    ax.plot(lons, lats, color="blue", linewidth=2.5, zorder=5)
    ax.plot(putin[1], putin[0], "go", markersize=12, zorder=6, label="Put-in")
    ax.plot(takeout[1], takeout[0], "rv", markersize=12, zorder=6, label="Take-out")

    pad = 0.03
    ax.set_xlim(min(lons) - pad, max(lons) + pad)
    ax.set_ylim(min(lats) - pad, max(lats) + pad)

    try:
        cx.add_basemap(ax, crs="EPSG:4326", source=cx.providers.OpenTopoMap, zoom=11)
    except Exception:
        with contextlib.suppress(Exception):
            cx.add_basemap(ax, crs="EPSG:4326", source=cx.providers.Esri.WorldTopoMap, zoom=11)

    ax.legend(loc="upper right", fontsize=12)
    title = f"{name} — {miles:.1f} miles" if name else f"Reach trace — {miles:.1f} miles"
    ax.set_title(title, fontsize=14)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")

    plt.tight_layout()
    plt.savefig(filename, dpi=150, bbox_inches="tight")
    plt.close()


def _resolve_huc4(huc4, putin, takeout, log):
    """Return ``huc4`` if non-None, otherwise auto-detect from putin then takeout.

    Raises ValueError if no HUC4 can be resolved from either endpoint.
    """
    if huc4 is not None:
        return huc4
    log("Finding HUC4...")
    detected = find_huc4(putin[0], putin[1]) or find_huc4(takeout[0], takeout[1])
    if not detected:
        raise ValueError(f"Could not find HUC4 for putin={putin}, takeout={takeout}")
    return detected


def _extend_and_trim_path(start_id, putin, takeout, by_hydroseq, by_nhdpid, src):
    """Trace downstream from ``start_id`` and trim to the segment nearest
    ``takeout``. Used when the exact takeout isn't on the main stem so
    ``trace_hydroseq`` returned None.

    Returns ``(path, geoms)`` where ``path`` is the trimmed list of
    NHDPlusIDs (inclusive on both ends) and ``geoms`` is the
    ``{nhdpid: ogr.Geometry}`` map produced by ``find_nearest_on_path``.

    Raises ValueError if no downstream segments can be traced.
    """
    current = by_nhdpid[start_id][0]
    extended_path = []
    for _ in range(2000):
        if current not in by_hydroseq:
            break
        nid, dn = by_hydroseq[current]
        extended_path.append(nid)
        if dn == 0:
            break
        current = dn

    if not extended_path:
        raise ValueError("Could not trace downstream from put-in.")

    end_idx, geoms = find_nearest_on_path(takeout[0], takeout[1], extended_path, src)
    pt = ogr.Geometry(ogr.wkbPoint)
    pt.AddPoint(putin[1], putin[0])
    start_idx = 0
    best_start = float("inf")
    for i, nid in enumerate(extended_path):
        if nid in geoms:
            d = pt.Distance(geoms[nid])
            if d < best_start:
                best_start = d
                start_idx = i

    return extended_path[start_idx : end_idx + 1], geoms, start_idx, end_idx


def _load_missing_geoms(src, path, geoms):
    """Hydrate any path segment geometries that ``find_nearest_on_path``
    didn't already load. Mutates ``geoms`` in place.
    """
    data_path, fl_layer_name, _, _ = src
    ds = ogr.Open(data_path)
    layer = ds.GetLayerByName(fl_layer_name)
    path_set = set(path)
    for feat in layer:
        nid = feat.GetField("NHDPlusID")
        if nid in path_set and nid not in geoms:
            geoms[nid] = feat.GetGeometryRef().Clone()
    ds = None


def trace_reach(putin, takeout, huc4=None, verbose=False):
    """Trace a stream reach and return a list of (lat, lon) vertices.

    Args:
        putin: (lat, lon) tuple for put-in point
        takeout: (lat, lon) tuple for take-out point
        huc4: HUC4 code string, or None to auto-detect
        verbose: print progress to stdout

    Returns:
        list of (lat, lon) tuples from put-in to take-out

    Raises:
        ValueError: if HUC4 not found or trace fails
    """
    log = print if verbose else lambda *a, **k: None

    huc4 = _resolve_huc4(huc4, putin, takeout, log)
    log(f"Using HUC4: {huc4}")

    src = data_source(huc4)
    log(f"  Data: {src[0]}")

    log("Loading VAA network index...")
    by_hydroseq, by_nhdpid = load_vaa(src)
    log(f"  {len(by_nhdpid):,} flowlines indexed")

    log("Finding nearest flowlines...")
    start_id, start_name, start_dist = find_nearest_flowline(putin[0], putin[1], src)
    end_id, end_name, end_dist = find_nearest_flowline(takeout[0], takeout[1], src)

    if start_id is None or end_id is None:
        raise ValueError("Could not find flowlines near the coordinates.")

    log(f"  Put-in:   {start_name or '(unnamed)'} (NHDPlusID {start_id}, {start_dist:.5f}°)")
    log(f"  Take-out: {end_name or '(unnamed)'} (NHDPlusID {end_id}, {end_dist:.5f}°)")

    log("Tracing downstream...")
    path = trace_hydroseq(start_id, end_id, by_hydroseq, by_nhdpid)

    if path is None:
        log("  Exact end not on main stem, tracing extended and trimming...")
        path, geoms, start_idx, end_idx = _extend_and_trim_path(
            start_id, putin, takeout, by_hydroseq, by_nhdpid, src
        )
        log(f"  Trimmed to {len(path)} segments (indices {start_idx}..{end_idx})")
    else:
        _, geoms = find_nearest_on_path(takeout[0], takeout[1], path, src)

    _load_missing_geoms(src, path, geoms)

    coords = build_trace(path, geoms, putin, takeout)
    log(f"Trace: {total_distance(coords):.1f} miles, {len(coords):,} points, {len(path)} segments")
    return coords


def main():
    parser = argparse.ArgumentParser(description="Trace a stream reach using NHD HR network data.")
    parser.add_argument("--putin", required=True, help="Put-in coordinates as LAT,LON")
    parser.add_argument("--takeout", required=True, help="Take-out coordinates as LAT,LON")
    parser.add_argument(
        "--name", default=None, help="Reach name (for map title, default: auto-detect)"
    )
    parser.add_argument(
        "--huc4", default=None, help="HUC4 code (default: auto-detect from coordinates)"
    )
    parser.add_argument(
        "--output", default=None, help="Output base name (default: derived from --name or 'trace')"
    )
    parser.add_argument(
        "--csv-only", action="store_true", help="Output CSV only, skip map generation"
    )
    args = parser.parse_args()

    putin = tuple(float(x) for x in args.putin.split(","))
    takeout = tuple(float(x) for x in args.takeout.split(","))

    if args.output:
        base = args.output
    elif args.name:
        base = args.name.lower().replace(" ", "_") + "_trace"
    else:
        base = "trace"

    coords = trace_reach(putin, takeout, huc4=args.huc4, verbose=True)
    name = args.name
    miles = total_distance(coords)

    csv_file = f"{base}.csv"
    write_csv(coords, csv_file)
    print(f"Wrote {csv_file}")

    if not args.csv_only:
        png_file = f"{base}.png"
        make_map(coords, putin, takeout, name, miles, png_file)
        print(f"Wrote {png_file}")

    print(f"\nPut-in:   {putin[0]:.6f}, {putin[1]:.6f}")
    print(f"Take-out: {takeout[0]:.6f}, {takeout[1]:.6f}")
    print(f"Distance: {miles:.1f} miles")


if __name__ == "__main__":
    main()
