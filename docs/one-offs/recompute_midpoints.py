#!/usr/bin/env python3
"""Recompute reach.latitude/longitude as the arc-length midpoint along reach.geom.

The canonical single-point for a reach is the point at 50% of cumulative geom
length, not the straight-line midpoint of put-in/take-out. Given winding river
paths, these can differ significantly.

Usage:
    python3 scripts/recompute_midpoints.py [--db PATH] [--apply] [--all]
                                           [--drift-threshold DEG]

Default target: reaches with NULL latitude/longitude, plus reaches whose current
single-point drifts more than --drift-threshold (default 0.05°) from the
straight-line midpoint of put-in/take-out (a proxy for "probably stale").

--all: recompute every reach that has geom populated (ignores drift threshold).
"""

import argparse
import os
import sqlite3
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))

from kayak.tracing.trace import haversine  # noqa: E402
from kayak.utils.simplify import parse_geom  # noqa: E402

DEFAULT_DB = os.environ.get("KAYAK_DB", "/home/pat/DB/kayak.db")


def arc_length_midpoint(coords):
    """Return (lon, lat) at 50% of cumulative length along the polyline.

    coords: list of (lon, lat) tuples.
    """
    if len(coords) < 2:
        return None
    seg_lens = []
    for i in range(len(coords) - 1):
        lon1, lat1 = coords[i]
        lon2, lat2 = coords[i + 1]
        seg_lens.append(haversine(lat1, lon1, lat2, lon2))
    total = sum(seg_lens)
    if total == 0:
        return coords[0]
    half = total / 2.0
    acc = 0.0
    for i, s in enumerate(seg_lens):
        if acc + s >= half:
            t = (half - acc) / s if s > 0 else 0.0
            lon1, lat1 = coords[i]
            lon2, lat2 = coords[i + 1]
            lon = lon1 + t * (lon2 - lon1)
            lat = lat1 + t * (lat2 - lat1)
            return (lon, lat)
        acc += s
    return coords[-1]


def select_targets(conn, drift_threshold, do_all):
    cur = conn.cursor()
    if do_all:
        cur.execute(
            "SELECT id, display_name, latitude, longitude, "
            "latitude_start, longitude_start, latitude_end, longitude_end, geom "
            "FROM reach WHERE geom IS NOT NULL AND geom <> '' "
            "ORDER BY id"
        )
        return cur.fetchall()
    cur.execute(
        """
        SELECT id, display_name, latitude, longitude,
               latitude_start, longitude_start, latitude_end, longitude_end, geom
        FROM reach
        WHERE geom IS NOT NULL AND geom <> ''
          AND (
            latitude IS NULL OR longitude IS NULL
            OR (
              latitude_start IS NOT NULL AND longitude_start IS NOT NULL
              AND latitude_end IS NOT NULL AND longitude_end IS NOT NULL
              AND MAX(
                ABS(latitude - (latitude_start + latitude_end) / 2.0),
                ABS(longitude - (longitude_start + longitude_end) / 2.0)
              ) > ?
            )
          )
        ORDER BY id
        """,
        (drift_threshold,),
    )
    return cur.fetchall()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true", help="write changes (default dry-run)")
    ap.add_argument("--all", action="store_true", help="recompute every reach with geom")
    ap.add_argument("--drift-threshold", type=float, default=0.05, help="degrees")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    rows = select_targets(conn, args.drift_threshold, args.all)
    if not rows:
        print("No reaches match target criteria.")
        return 0

    print(
        f"{'id':>5}  {'display_name':<24}  {'old_lat':>10} {'old_lon':>11}   "
        f"{'new_lat':>10} {'new_lon':>11}   {'delta_deg':>9}"
    )
    print("-" * 95)

    updates = []
    for row in rows:
        rid, name, old_lat, old_lon, lps, lpl, lts, ltl, geom = row
        coords = parse_geom(geom)
        mid = arc_length_midpoint(coords)
        if mid is None:
            print(f"{rid:>5}  {(name or '')[:24]:<24}  <geom has <2 points, skipped>")
            continue
        new_lon, new_lat = mid
        new_lon = round(new_lon, 6)
        new_lat = round(new_lat, 6)
        if old_lat is None or old_lon is None:
            delta = "(null)"
        else:
            d = max(abs(old_lat - new_lat), abs(old_lon - new_lon))
            delta = f"{d:.4f}"
        old_lat_s = f"{old_lat:.6f}" if old_lat is not None else "NULL"
        old_lon_s = f"{old_lon:.6f}" if old_lon is not None else "NULL"
        print(
            f"{rid:>5}  {(name or '')[:24]:<24}  "
            f"{old_lat_s:>10} {old_lon_s:>11}   "
            f"{new_lat:>10.6f} {new_lon:>11.6f}   {delta:>9}"
        )
        updates.append((new_lat, new_lon, rid))

    print(f"\n{len(updates)} reach(es) would be updated.")

    if not args.apply:
        print("Dry-run only. Pass --apply to write changes.")
        return 0

    cur = conn.cursor()
    cur.executemany(
        "UPDATE reach SET latitude = ?, longitude = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        updates,
    )
    conn.commit()
    print(f"Applied {cur.rowcount} update(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
