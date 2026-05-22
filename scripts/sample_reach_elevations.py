#!/usr/bin/env python3
"""Phase 2A: sample reach elevations from local 3DEP DEM tiles.

For each reach with a non-NULL geom, walk the LINESTRING, resample at a
uniform along-channel interval, query the appropriate DEM tile with
bilinear interpolation, and write a per-reach JSON cache:

    Elevation-cache/reach_<id>.json
    {
      "reach_id": 407,
      "aw_id": 2868,
      "sampled_at": "2026-05-22T22:00:00+00:00",
      "interval_m": 50,
      "dem_sources_used": {"1arc3": 412, "1m": 0},
      "points": [
        {"d_mi": 0.0,   "lat": 44.1048, "lon": -122.0218, "elev_ft": 2197.4, "src": "1arc3"},
        {"d_mi": 0.031, "lat": 44.1056, "lon": -122.0205, "elev_ft": 2192.1, "src": "1arc3"},
        ...
      ]
    }

Phase 2A v1 supports the 1/3 arc-second tier only. 1 m LIDAR support is
folded in later by extending the tile-source lookup (the JSON cache's
``src`` per-point tag is the contract for downstream Phase 2B).

Idempotent: cache file is regenerated only when missing, --force is
passed, or its sampled_at predates reach.updated_at.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

DEFAULT_DB = os.environ.get("KAYAK_DB", "/Users/pat/tpw/DB/kayak.db")
DEFAULT_DEM_CACHE = Path("DEM-cache")
DEFAULT_OUT_CACHE = Path("Elevation-cache")
M_TO_FT = 3.28083989501

# Deferred rasterio import (heavy + only needed when sampling) — at call sites.


def _parse_geom(geom: str) -> list[tuple[float, float]]:
    """Parse our raw 'lon lat,lon lat,…' geom into a list of (lon, lat)."""
    out: list[tuple[float, float]] = []
    for pair in geom.split(","):
        pair = pair.strip()
        if not pair:
            continue
        parts = pair.split()
        if len(parts) != 2:
            continue
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue
        out.append((lon, lat))
    return out


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    R = 6371008.8  # mean earth radius (m)
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _interpolate(
    lat1: float, lon1: float, lat2: float, lon2: float, t: float
) -> tuple[float, float]:
    """Linear interpolation along a great-circle approximation.

    For the short (~10-1000 m) segments we use, linear interp in
    lat/lon is well within bilinear-DEM noise. Avoids the geodesic
    library dependency.
    """
    return (lat1 + (lat2 - lat1) * t, lon1 + (lon2 - lon1) * t)


def walk_reach(geom: str, interval_m: float) -> Iterator[tuple[float, float, float]]:
    """Yield ``(d_mi, lat, lon)`` triples spaced ~interval_m along the polyline.

    The first yield is the put-in vertex; subsequent yields are
    interpolated to fall on the polyline at the requested spacing.
    Doesn't always end exactly on the take-out — emits the last
    interpolated point ≤ length and lets the caller decide whether to
    append the literal take-out.
    """
    verts = _parse_geom(geom)
    if len(verts) < 2:
        return

    cum_m = 0.0
    next_emit_m = 0.0
    yield (0.0, verts[0][1], verts[0][0])
    next_emit_m += interval_m

    for i in range(len(verts) - 1):
        lon1, lat1 = verts[i]
        lon2, lat2 = verts[i + 1]
        seg_len_m = _haversine_m(lat1, lon1, lat2, lon2)
        if seg_len_m == 0:
            continue
        seg_start_m = cum_m
        while next_emit_m <= cum_m + seg_len_m:
            t = (next_emit_m - seg_start_m) / seg_len_m
            lat, lon = _interpolate(lat1, lon1, lat2, lon2, t)
            yield (next_emit_m / 1609.344, lat, lon)
            next_emit_m += interval_m
        cum_m += seg_len_m

    # Always emit the take-out as the final point if the loop fell short
    last_lat = verts[-1][1]
    last_lon = verts[-1][0]
    final_mi = cum_m / 1609.344
    # Avoid emitting twice if the spacing landed exactly on the take-out
    if next_emit_m - interval_m < cum_m - 1.0:
        yield (final_mi, last_lat, last_lon)


# -------------------------------------------------------------------------
# DEM tile index + bilinear sampling
# -------------------------------------------------------------------------


def build_tile_index(dem_cache: Path) -> list[dict]:
    """Scan DEM-cache/1arc3/ (and later /1m/) for available tiles.

    Returns a list of records with keys: ``path``, ``src``, ``bounds``
    (left, bottom, right, top in WGS84).
    """
    import rasterio

    index: list[dict] = []
    for src_tier in ("1arc3", "1m"):
        tile_dir = dem_cache / src_tier
        if not tile_dir.exists():
            continue
        for path in sorted(tile_dir.rglob("*.tif")):
            try:
                with rasterio.open(path) as ds:
                    b = ds.bounds
                    # Verify CRS is geographic (lat/lon) — 3DEP tiles
                    # should be EPSG:4269 (NAD83) or 4326 (WGS84); the
                    # difference at our latitudes is < 1 m.
                    if not ds.crs or ds.crs.to_epsg() not in (4269, 4326, 4267):
                        print(
                            f"  skip {path}: non-geographic CRS {ds.crs}",
                            file=sys.stderr,
                        )
                        continue
                    index.append(
                        {
                            "path": str(path),
                            "src": src_tier,
                            "bounds": (b.left, b.bottom, b.right, b.top),
                        }
                    )
            except Exception as exc:
                print(f"  skip {path}: {exc}", file=sys.stderr)
    return index


@lru_cache(maxsize=8)
def _open_dataset(path: str):
    import rasterio

    return rasterio.open(path)


def find_tile(index: list[dict], lon: float, lat: float, prefer_src: str = "1m") -> dict | None:
    """Return the index entry whose bounds contain (lon, lat).

    Preference order: 1 m LIDAR first (if any tile covers the point),
    then 1/3 arc-second. Returns None if no tile covers.
    """
    cand_1m = None
    cand_other = None
    for tile in index:
        left, bot, right, top = tile["bounds"]
        if left <= lon <= right and bot <= lat <= top:
            if tile["src"] == prefer_src:
                cand_1m = tile
                break
            elif cand_other is None:
                cand_other = tile
    return cand_1m or cand_other


def sample_bilinear(tile_path: str, lon: float, lat: float) -> float | None:
    """Sample a single tile at (lon, lat) using bilinear interpolation.

    Returns elevation in the tile's native vertical unit (meters for
    3DEP), or None if the sample falls inside but the cell is a NoData
    pixel.
    """
    import numpy as np
    from rasterio.windows import Window

    ds = _open_dataset(tile_path)
    # Convert (lon, lat) to fractional (row, col)
    col_f, row_f = ~ds.transform * (lon, lat)
    col_i = math.floor(col_f)
    row_i = math.floor(row_f)
    if not (0 <= col_i < ds.width - 1 and 0 <= row_i < ds.height - 1):
        return None
    # Read 2x2 window covering the four surrounding cells
    window = Window(col_i, row_i, 2, 2)
    block = ds.read(1, window=window, masked=False)
    if block.shape != (2, 2):
        return None

    nodata = ds.nodata
    if nodata is not None and np.any(block == nodata):
        return None
    # Bilinear weights
    dx = col_f - col_i
    dy = row_f - row_i
    top = block[0, 0] * (1 - dx) + block[0, 1] * dx
    bot = block[1, 0] * (1 - dx) + block[1, 1] * dx
    return float(top * (1 - dy) + bot * dy)


# -------------------------------------------------------------------------
# Per-reach driver
# -------------------------------------------------------------------------


def process_reach(
    reach: sqlite3.Row,
    index: list[dict],
    interval_m: float,
    out_dir: Path,
) -> tuple[int, dict[str, int], int]:
    """Sample one reach. Returns (point_count, sources_histogram, missed_count)."""
    points: list[dict] = []
    sources: dict[str, int] = {"1arc3": 0, "1m": 0}
    missed = 0
    for d_mi, lat, lon in walk_reach(reach["geom"], interval_m):
        tile = find_tile(index, lon, lat)
        if tile is None:
            missed += 1
            continue
        elev_m = sample_bilinear(tile["path"], lon, lat)
        if elev_m is None:
            missed += 1
            continue
        elev_ft = round(elev_m * M_TO_FT, 1)
        points.append(
            {
                "d_mi": round(d_mi, 4),
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "elev_ft": elev_ft,
                "src": tile["src"],
            }
        )
        sources[tile["src"]] = sources.get(tile["src"], 0) + 1

    cache = {
        "reach_id": reach["id"],
        "aw_id": reach["aw_id"],
        "display_name": reach["display_name"],
        "sampled_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "interval_m": interval_m,
        "dem_sources_used": sources,
        "missed_sample_count": missed,
        "points": points,
    }
    out_path = out_dir / f"reach_{reach['id']}.json"
    with open(out_path, "w") as fh:
        json.dump(cache, fh)
    return len(points), sources, missed


def should_skip(cache_path: Path, reach_updated_at: str | None, force: bool) -> bool:
    """Skip re-sampling if the cache is fresher than reach.updated_at."""
    if force or not cache_path.exists():
        return False
    if not reach_updated_at:
        return True  # no timestamp to compare; assume cache is fine
    try:
        with open(cache_path) as fh:
            existing = json.load(fh)
        sampled_at = existing.get("sampled_at", "")
        return sampled_at >= reach_updated_at
    except (OSError, json.JSONDecodeError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--reach-ids", help="Comma-separated reach IDs (default: all with geom)")
    ap.add_argument("--interval-m", type=float, default=50.0)
    ap.add_argument("--cache-dir", default=str(DEFAULT_OUT_CACHE), type=Path)
    ap.add_argument("--dem-cache", default=str(DEFAULT_DEM_CACHE), type=Path)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    args.cache_dir.mkdir(parents=True, exist_ok=True)

    print(f"Scanning DEM cache at {args.dem_cache}/ ...")
    index = build_tile_index(args.dem_cache)
    print(
        f"  Indexed {len(index)} tiles "
        f"({sum(1 for t in index if t['src'] == '1arc3')} x 1arc3, "
        f"{sum(1 for t in index if t['src'] == '1m')} x 1m)"
    )
    if not index:
        print("ERROR: no DEM tiles found. Run scripts/fetch_dem_tiles.py first.", file=sys.stderr)
        return 2

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    q = (
        "SELECT id, aw_id, display_name, geom, updated_at FROM reach "
        "WHERE geom IS NOT NULL AND geom != ''"
    )
    params: list = []
    if args.reach_ids:
        ids = [int(x) for x in args.reach_ids.split(",")]
        q += f" AND id IN ({','.join('?' * len(ids))})"
        params = ids
    q += " ORDER BY id"
    rows = conn.execute(q, params).fetchall()
    print(f"Scope: {len(rows)} reach(es)")

    total_points = 0
    total_missed = 0
    skipped = 0
    for i, r in enumerate(rows, start=1):
        cache_path = args.cache_dir / f"reach_{r['id']}.json"
        if should_skip(cache_path, r["updated_at"], args.force):
            skipped += 1
            continue
        n_points, _sources, missed = process_reach(r, index, args.interval_m, args.cache_dir)
        total_points += n_points
        total_missed += missed
        if i % 25 == 0 or i == len(rows):
            print(f"  [{i}/{len(rows)}] {r['display_name']!r} → {n_points} pts ({missed} missed)")
    print()
    print(f"Done. {len(rows) - skipped} reaches sampled, {skipped} skipped (cache current).")
    print(f"Total points: {total_points}; total missed (no tile coverage): {total_missed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
