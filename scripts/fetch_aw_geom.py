#!/usr/bin/env python3
"""Fetch reach geometry from American Whitewater vector tiles.

AW serves reach geometries as Mapbox Vector Tiles (MVT) at:
    https://api.americanwhitewater.org/tiles/{z}/{x}/{y}.mvt/

This script fetches tiles covering a reach's put-in/take-out bounding box,
extracts the LineString geometry for the given AW reach ID, chains the
per-tile segments in order, and optionally saves to the database.

Usage:
    # Preview geometry for AW reach 2726
    python3 scripts/fetch_aw_geom.py --aw-id 2726

    # Save to database reach 526
    python3 scripts/fetch_aw_geom.py --aw-id 2726 --reach-id 526 --save

    # Fetch using explicit bounding box (if put-in/take-out unknown)
    python3 scripts/fetch_aw_geom.py --aw-id 2726 --bbox -122.66,43.62,-122.58,43.65

    # Also fetch put-in/take-out from AW tRPC API
    python3 scripts/fetch_aw_geom.py --aw-id 2726 --reach-id 526 --save --fetch-poi
"""

import argparse
import gzip
import json
import math
import sys
import urllib.request

try:
    import mapbox_vector_tile
except ImportError:
    print("Error: pip install mapbox-vector-tile", file=sys.stderr)
    sys.exit(1)


TILE_URL = "https://api.americanwhitewater.org/tiles/{z}/{x}/{y}.mvt/"
TRPC_URL = "https://trpc-api.americanwhitewater.org/reach/reachDetail"
ZOOM = 14
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible)",
    "Referer": "https://www.americanwhitewater.org/",
}


def lonlat_to_tile(lon, lat, z):
    n = 2**z
    x = int((lon + 180) / 360 * n)
    y = int(
        (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi)
        / 2
        * n
    )
    return x, y


def tile_to_lonlat(px, py, tx, ty, z, extent):
    """Convert MVT pixel coords to lon/lat.

    The mapbox_vector_tile decoder returns y with 0 at the bottom of the tile,
    but the tile grid has y=0 at the top, so we flip py.
    """
    n = 2**z
    lon = (tx + px / extent) / n * 360 - 180
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * (ty + 1 - py / extent) / n)))
    lat = math.degrees(lat_rad)
    return lon, lat


def dist(p1, p2):
    return (p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2


def fetch_poi(aw_id):
    """Fetch put-in/take-out from AW tRPC API. Returns (putin, takeout) as (lon, lat) tuples."""
    input_json = json.dumps({"0": {"json": {"reachID": str(aw_id)}}})
    url = f"{TRPC_URL}?batch=1&input={urllib.parse.quote(input_json)}"
    req = urllib.request.Request(url, headers=HEADERS)
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.loads(resp.read())
    reach = data[0]["result"]["data"]["json"]

    putin = takeout = None
    for poi in reach.get("pointOfInterests", []):
        loc = poi["location"]
        point = (float(loc["longitude"]), float(loc["latitude"]))
        if poi["type"] == "put-in":
            putin = point
        elif poi["type"] == "takeout":
            takeout = point

    return putin, takeout


def fetch_tile_segments(aw_id, min_lon, min_lat, max_lon, max_lat, z=ZOOM):
    """Fetch all tile segments for a given AW reach ID within a bounding box."""
    x1, y1 = lonlat_to_tile(min_lon, max_lat, z)  # top-left
    x2, y2 = lonlat_to_tile(max_lon, min_lat, z)  # bottom-right

    segments = []
    for tx in range(min(x1, x2), max(x1, x2) + 1):
        for ty in range(min(y1, y2), max(y1, y2) + 1):
            url = TILE_URL.format(z=z, x=tx, y=ty)
            req = urllib.request.Request(url, headers=HEADERS)
            try:
                resp = urllib.request.urlopen(req, timeout=15)
                raw = resp.read()
                try:
                    data = gzip.decompress(raw)
                except Exception:
                    data = raw
                decoded = mapbox_vector_tile.decode(data)
                if "reach-segments" not in decoded:
                    continue
                extent = decoded["reach-segments"]["extent"]
                for feat in decoded["reach-segments"]["features"]:
                    if feat.get("properties", {}).get("id") == aw_id:
                        coords = feat["geometry"]["coordinates"]
                        points = [
                            (
                                round(tile_to_lonlat(px, py, tx, ty, z, extent)[0], 6),
                                round(tile_to_lonlat(px, py, tx, ty, z, extent)[1], 6),
                            )
                            for px, py in coords
                        ]
                        segments.append(points)
            except urllib.error.HTTPError:
                pass
            except Exception as e:
                print(f"  Warning: tile {tx},{ty}: {e}", file=sys.stderr)

    return segments


def chain_segments(segments, putin=None, takeout=None):
    """Chain tile segments into a single ordered LineString.

    Orients and orders segments so the line flows from put-in to take-out.
    If putin and takeout are provided, segments are sorted by their projection
    onto the put-in → take-out axis, which handles meanders correctly.
    Otherwise falls back to greedy nearest-endpoint chaining.
    """
    if not segments:
        return []

    if putin and takeout:
        # Project each segment's midpoint onto the putin→takeout axis
        dx = takeout[0] - putin[0]
        dy = takeout[1] - putin[1]
        axis_len_sq = dx * dx + dy * dy
        if axis_len_sq == 0:
            axis_len_sq = 1

        def project(pt):
            return ((pt[0] - putin[0]) * dx + (pt[1] - putin[1]) * dy) / axis_len_sq

        # Orient each segment so it flows in the putin→takeout direction
        for i, seg in enumerate(segments):
            if project(seg[0]) > project(seg[-1]):
                segments[i] = list(reversed(seg))

        # Sort by projection of segment start point
        segments.sort(key=lambda s: project(s[0]))

        # Chain in sorted order, connecting endpoints
        chain = segments[0][:]
        for seg in segments[1:]:
            if dist(chain[-1], seg[0]) < 0.0001:
                chain.extend(seg[1:])
            else:
                chain.extend(seg)
        return chain

    if putin:
        # Orient each segment: the end nearest putin should be the start
        for i, seg in enumerate(segments):
            if dist(putin, seg[-1]) < dist(putin, seg[0]):
                segments[i] = list(reversed(seg))
        # Sort by distance from putin
        segments.sort(key=lambda s: dist(putin, s[0]), reverse=False)
    else:
        # Default: orient east-to-west (longitude decreasing)
        for i, seg in enumerate(segments):
            if seg[0][0] < seg[-1][0]:
                segments[i] = list(reversed(seg))
        segments.sort(key=lambda s: s[0][0], reverse=True)

    # Chain: greedily connect nearest endpoints
    remaining = list(segments)
    if putin:
        best_idx = min(range(len(remaining)), key=lambda i: dist(putin, remaining[i][0]))
    else:
        best_idx = 0
    chain = remaining.pop(best_idx)[:]

    while remaining:
        end = chain[-1]
        best_idx = None
        best_d = float("inf")
        reverse = False
        for i, s in enumerate(remaining):
            d_start = dist(end, s[0])
            d_end = dist(end, s[-1])
            if d_start < best_d:
                best_d = d_start
                best_idx = i
                reverse = False
            if d_end < best_d:
                best_d = d_end
                best_idx = i
                reverse = True

        seg = remaining.pop(best_idx)
        pts = list(reversed(seg)) if reverse else seg
        if dist(chain[-1], pts[0]) < 0.0001:
            pts = pts[1:]
        chain.extend(pts)

    return chain


def main():
    parser = argparse.ArgumentParser(description="Fetch AW reach geometry from vector tiles")
    parser.add_argument("--aw-id", type=int, required=True, help="AW reach ID")
    parser.add_argument("--reach-id", type=int, help="Local DB reach ID to update")
    parser.add_argument("--save", action="store_true", help="Save geometry to database")
    parser.add_argument(
        "--fetch-poi", action="store_true", help="Fetch put-in/take-out from AW tRPC API"
    )
    parser.add_argument("--bbox", help="Bounding box: min_lon,min_lat,max_lon,max_lat")
    parser.add_argument("--zoom", type=int, default=ZOOM, help=f"Tile zoom level (default {ZOOM})")
    args = parser.parse_args()

    putin = takeout = None

    if args.fetch_poi:
        print(f"Fetching POI for AW reach {args.aw_id}...")
        try:
            putin, takeout = fetch_poi(args.aw_id)
            if putin:
                print(f"  Put-in: {putin[1]:.6f}, {putin[0]:.6f}")
            if takeout:
                print(f"  Take-out: {takeout[1]:.6f}, {takeout[0]:.6f}")
        except Exception as e:
            print(f"  Warning: could not fetch POI: {e}", file=sys.stderr)

    if args.bbox:
        parts = [float(x) for x in args.bbox.split(",")]
        min_lon, min_lat, max_lon, max_lat = parts
    elif putin and takeout:
        margin = 0.01
        min_lon = min(putin[0], takeout[0]) - margin
        min_lat = min(putin[1], takeout[1]) - margin
        max_lon = max(putin[0], takeout[0]) + margin
        max_lat = max(putin[1], takeout[1]) + margin
    else:
        print("Error: provide --bbox or --fetch-poi to determine tile range", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching tiles for AW reach {args.aw_id} at zoom {args.zoom}...")
    segments = fetch_tile_segments(args.aw_id, min_lon, min_lat, max_lon, max_lat, args.zoom)
    print(f"  Found {len(segments)} tile segments")

    if not segments:
        print("No geometry found.", file=sys.stderr)
        sys.exit(1)

    chain = chain_segments(segments, putin=putin, takeout=takeout)
    geom_str = ",".join(f"{lon} {lat}" for lon, lat in chain)

    print(f"  Chained: {len(chain)} points")
    print(f"  Start: {chain[0]}")
    print(f"  End: {chain[-1]}")

    # Continuity check: flag gaps between consecutive points
    MAX_GAP = 0.01  # ~1km in degrees
    gaps = []
    for i in range(1, len(chain)):
        d = math.sqrt(dist(chain[i - 1], chain[i]))
        if d > MAX_GAP:
            gaps.append((i, d))
    if gaps:
        print(f"  WARNING: {len(gaps)} gap(s) in trace (>{MAX_GAP:.3f}°):")
        for idx, d in gaps[:10]:
            print(f"    point {idx}: {d:.4f}° between {chain[idx - 1]} and {chain[idx]}")
        if len(gaps) > 10:
            print(f"    ... and {len(gaps) - 10} more")
    else:
        print("  Continuity: OK (no gaps)")

    # Check start/end proximity to put-in/take-out
    if putin:
        d_pi = math.sqrt(dist(putin, chain[0]))
        print(
            f"  Start→put-in: {d_pi:.4f}°{'  OK' if d_pi < 0.01 else '  WARNING: far from put-in'}"
        )
    if takeout:
        d_to = math.sqrt(dist(takeout, chain[-1]))
        print(
            f"  End→take-out: {d_to:.4f}°{'  OK' if d_to < 0.01 else '  WARNING: far from take-out'}"
        )

    if args.save:
        if not args.reach_id:
            print("Error: --reach-id required with --save", file=sys.stderr)
            sys.exit(1)

        from kayak.config import DATABASE_URL
        from kayak.db.engine import get_session
        from kayak.db.models import Reach

        with get_session(DATABASE_URL) as s:
            r = s.query(Reach).filter(Reach.id == args.reach_id).one()
            r.geom = geom_str
            if putin:
                r.latitude_start = round(putin[1], 6)
                r.longitude_start = round(putin[0], 6)
            if takeout:
                r.latitude_end = round(takeout[1], 6)
                r.longitude_end = round(takeout[0], 6)
            if putin and takeout:
                r.latitude = round((putin[1] + takeout[1]) / 2, 6)
                r.longitude = round((putin[0] + takeout[0]) / 2, 6)
            s.commit()
            print(f"  Saved to reach {args.reach_id}")
    else:
        print(f"\nGeom string ({len(geom_str)} chars):")
        print(geom_str)


if __name__ == "__main__":
    main()
