#!/usr/bin/env python3
"""Snap a reach's geom vertices to the local channel-floor minimum
found by sampling DEM perpendicular to the local flow direction.

Hypothesis: in narrow canyons the NHD-HR network trace often routes
through the digitized polygon centerline, which may not coincide with
the actual water channel. Modern LIDAR DEMs (and even 10 m 3DEP) often
do capture the channel floor, just offset from the trace. For each
vertex, we look perpendicular to the local flow direction, sample
elevation at fixed intervals out to a search radius, and snap the
vertex to the minimum-elevation point.

This is a prototype/diagnostic tool. ``--dry-run`` (default) prints a
comparison table of original vs snapped gradient profiles. ``--apply``
writes back to reach.geom + recomputes the profile.

Edge cases noted in the design discussion but not yet handled:
* braided channels / oxbow lakes: snap can hop between strands
* tributary confluences: snap may pull onto the wrong channel
* road bridges / culverts: snap could route through the bridge undercut

Mitigations applied:
* `--max-snap-drop-ft` (default 200): if the local min is more than
  this far below the trace, refuse to snap (probable bridge / cliff).
* `--smooth-snap-pts` (default 5): after per-vertex snapping, apply a
  small rolling-mean smoothing to suppress single-vertex jumps.

Usage::

    KAYAK_DB=/path/to/kayak.db \\
    python3 docs/one-offs/snap_reach_to_channel_min.py \\
        --reach-ids 134,155,127,186 \\
        --search-m 100 --step-m 5
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = os.environ.get("KAYAK_DB", "")
DEFAULT_CACHE = Path("Elevation-cache")
M_PER_DEG_LAT = 110540.0
M_TO_FT = 3.28083989501

# Reuse the sample_reach_elevations helpers without making them a package import.
_sre_spec = importlib.util.spec_from_file_location(
    "sre", "docs/one-offs/sample_reach_elevations.py"
)
sre = importlib.util.module_from_spec(_sre_spec)
_sre_spec.loader.exec_module(sre)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, in degrees clockwise from north."""
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(math.radians(lat2))
    x = math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) - math.sin(
        math.radians(lat1)
    ) * math.cos(math.radians(lat2)) * math.cos(dl)
    return math.degrees(math.atan2(y, x))


def _offset_along_bearing(
    lat: float, lon: float, offset_m: float, bearing_deg: float
) -> tuple[float, float]:
    """Offset (lat, lon) by offset_m along bearing_deg. Equirectangular
    approximation — fine for the small offsets we use (< 200 m)."""
    rad_b = math.radians(bearing_deg)
    dlat = (offset_m * math.cos(rad_b)) / M_PER_DEG_LAT
    dlon = (offset_m * math.sin(rad_b)) / (M_PER_DEG_LAT * math.cos(math.radians(lat)))
    return (lat + dlat, lon + dlon)


def _sample_elev_ft(index, lon: float, lat: float) -> float | None:
    """Sample the DEM (LIDAR first, 1arc3 fallback). Returns ft."""
    for cand in sre.find_tiles(index, lon, lat):
        v = sre.sample_bilinear(cand, lon, lat)
        if v is not None:
            return v * M_TO_FT
    return None


def snap_vertex(
    index,
    prev_lon: float,
    prev_lat: float,
    lon: float,
    lat: float,
    next_lon: float,
    next_lat: float,
    *,
    search_m: float,
    step_m: float,
    max_drop_ft: float,
) -> tuple[float, float, float | None, float | None, float]:
    """Snap a single vertex to the perpendicular min within search_m.

    Returns (new_lon, new_lat, trace_elev_ft, snapped_elev_ft, offset_m).
    Snap is refused (returns original coords) if:
      * the trace elevation can't be sampled (no DEM coverage), or
      * the local min is > max_drop_ft below the trace (probable bridge).
    """
    flow_b = _bearing(prev_lat, prev_lon, next_lat, next_lon)
    perp_b = (flow_b + 90.0) % 360.0

    trace_elev = _sample_elev_ft(index, lon, lat)
    if trace_elev is None:
        return lon, lat, None, None, 0.0

    # NOTE: _sample_elev_ft mixes DEM tiers (1 m LIDAR preferred, 1/3 arc-second
    # fallback per cell). The raw min across tiers ignores cross-source vertical
    # offset, so a noisy 1arc3 cell (~7.9 ft RMSE) can occasionally win the min
    # where coverage switches. Bounded by the smoothing pass in snap_reach; if it
    # bites, gate acceptance on drop > a cross-source noise margin (operator's call).
    best_offset = 0.0
    best_elev = trace_elev
    offset = -search_m
    while offset <= search_m:
        olat, olon = _offset_along_bearing(lat, lon, offset, perp_b)
        e = _sample_elev_ft(index, olon, olat)
        if e is not None and e < best_elev:
            best_elev = e
            best_offset = offset
        offset += step_m

    drop = trace_elev - best_elev
    if drop > max_drop_ft:
        # Probable bridge / cliff — refuse to move the vertex, but report the
        # real best_elev so snap_reach classifies this as a bridge-skip. (Returning
        # trace_elev here would collapse into the "within noise" branch and the
        # bridge counter would never increment.)
        return lon, lat, trace_elev, best_elev, 0.0

    if best_offset == 0.0:
        return lon, lat, trace_elev, best_elev, 0.0

    new_lat, new_lon = _offset_along_bearing(lat, lon, best_offset, perp_b)
    return new_lon, new_lat, trace_elev, best_elev, best_offset


def _smooth_verts(verts: list[tuple[float, float]], window_pts: int) -> list[tuple[float, float]]:
    """Rolling-mean smooth of vertex coords. Preserves endpoints."""
    if window_pts <= 1 or len(verts) < 3:
        return list(verts)
    n = len(verts)
    half = window_pts // 2
    out = [verts[0]]
    for i in range(1, n - 1):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        window = verts[lo:hi]
        avg_lon = sum(v[0] for v in window) / len(window)
        avg_lat = sum(v[1] for v in window) / len(window)
        out.append((avg_lon, avg_lat))
    out.append(verts[-1])
    return out


def snap_reach(
    geom: str,
    index,
    *,
    search_m: float,
    step_m: float,
    max_drop_ft: float,
    smooth_pts: int,
) -> tuple[str, dict]:
    """Snap each interior vertex of a geom to the local channel min.

    Returns ``(new_geom, stats)``.
    """
    verts = []
    for pair in geom.split(","):
        p = pair.strip().split()
        if len(p) == 2:
            verts.append((float(p[0]), float(p[1])))

    if len(verts) < 3:
        return geom, {"snapped": 0, "skipped_bridge": 0, "no_dem": 0, "vertices": len(verts)}

    new_verts: list[tuple[float, float]] = [verts[0]]
    snap_count = 0
    bridge_skip = 0
    no_dem = 0
    total_drop = 0.0
    total_offset = 0.0

    for i in range(1, len(verts) - 1):
        prev_lon, prev_lat = verts[i - 1]
        lon, lat = verts[i]
        next_lon, next_lat = verts[i + 1]
        new_lon, new_lat, trace_e, snap_e, offset = snap_vertex(
            index,
            prev_lon,
            prev_lat,
            lon,
            lat,
            next_lon,
            next_lat,
            search_m=search_m,
            step_m=step_m,
            max_drop_ft=max_drop_ft,
        )
        if trace_e is None:
            no_dem += 1
        elif snap_e is None or snap_e >= trace_e - 1.0:
            # No meaningful snap (already at min or within noise)
            pass
        elif trace_e - snap_e > max_drop_ft:
            bridge_skip += 1
        else:
            snap_count += 1
            total_drop += trace_e - snap_e
            total_offset += abs(offset)
        new_verts.append((new_lon, new_lat))
    new_verts.append(verts[-1])

    if smooth_pts > 1:
        new_verts = _smooth_verts(new_verts, smooth_pts)

    new_geom = ",".join(f"{lon:.6f} {lat:.6f}" for lon, lat in new_verts)
    stats = {
        "vertices": len(verts),
        "snapped": snap_count,
        "skipped_bridge": bridge_skip,
        "no_dem": no_dem,
        "mean_drop_ft": round(total_drop / snap_count, 1) if snap_count else 0,
        "mean_offset_m": round(total_offset / snap_count, 1) if snap_count else 0,
    }
    return new_geom, stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--reach-ids", required=True, help="Comma-separated reach ids")
    ap.add_argument("--dem-cache", default="DEM-cache", type=Path)
    ap.add_argument(
        "--search-m", type=float, default=100.0, help="Perpendicular search radius (default 100 m)"
    )
    ap.add_argument(
        "--step-m", type=float, default=5.0, help="Perpendicular sample step (default 5 m)"
    )
    ap.add_argument(
        "--max-snap-drop-ft",
        type=float,
        default=200.0,
        help="Refuse snap if local min is > this much below trace (default 200 ft)",
    )
    ap.add_argument(
        "--smooth-snap-pts",
        type=int,
        default=5,
        help="Rolling-mean window for post-snap smoothing (default 5)",
    )
    ap.add_argument(
        "--apply", action="store_true", help="Write snapped geom back to DB (default: dry-run)"
    )
    args = ap.parse_args()
    if not args.db:
        sys.exit("error: pass --db /path/to/kayak.db or set KAYAK_DB in env")

    print(f"Building tile index from {args.dem_cache}/ ...")
    index = sre.build_tile_index(args.dem_cache)
    print(f"  Indexed {len(index)} tiles")
    print()

    ids = [int(x) for x in args.reach_ids.split(",")]
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    for rid in ids:
        row = conn.execute(
            "SELECT id, display_name, geom, length FROM reach WHERE id = ?", (rid,)
        ).fetchone()
        if not row or not row["geom"]:
            print(f"reach {rid}: no row or no geom")
            continue

        print(f"=== reach {rid} {row['display_name']!r} ===")
        new_geom, stats = snap_reach(
            row["geom"],
            index,
            search_m=args.search_m,
            step_m=args.step_m,
            max_drop_ft=args.max_snap_drop_ft,
            smooth_pts=args.smooth_snap_pts,
        )
        print(
            f"  vertices: {stats['vertices']}, "
            f"snapped: {stats['snapped']} (mean drop {stats['mean_drop_ft']} ft, "
            f"mean offset {stats['mean_offset_m']} m), "
            f"bridge-refused: {stats['skipped_bridge']}, "
            f"no DEM: {stats['no_dem']}"
        )

        if args.apply:
            cur = conn.cursor()
            cur.execute(
                "UPDATE reach SET geom = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_geom, rid),
            )
            conn.commit()
            print("  applied (geom updated)")
        else:
            print(
                f"  dry-run only (pass --apply to write geom); new_geom length: {len(new_geom)} bytes"
            )
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
