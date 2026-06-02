#!/usr/bin/env python3
"""One-off: NHD HR trace the two halves of the split McKenzie reach.

Reaches 42 (Paradise->Bruckart) and 421 (Bruckart->Finn Rock) already have
their put-in/take-out columns set in the DB; this traces each along NHD HR,
writes reach.geom + reach.length, and recomputes reach.latitude/longitude as
the arc-length midpoint of the geom (per scripts/recompute_midpoints.py).

Run under brew python (osgeo + kayak): /opt/homebrew/bin/python3
"""

from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from kayak.tracing import trace as impl

DB = os.environ.get("KAYAK_DB", "/Users/pat/tpw/DB/kayak.db")
REACH_IDS = [42, 421]


def arc_length_midpoint(coords_lonlat):
    """(lon, lat) at 50% cumulative length. coords_lonlat: [(lon, lat), ...]."""
    if len(coords_lonlat) < 2:
        return None
    seg = []
    for i in range(len(coords_lonlat) - 1):
        lon1, lat1 = coords_lonlat[i]
        lon2, lat2 = coords_lonlat[i + 1]
        seg.append(impl.haversine(lat1, lon1, lat2, lon2))
    total = sum(seg)
    if total == 0:
        return coords_lonlat[0]
    half = total / 2.0
    acc = 0.0
    for i, s in enumerate(seg):
        if acc + s >= half:
            t = (half - acc) / s if s > 0 else 0.0
            lon1, lat1 = coords_lonlat[i]
            lon2, lat2 = coords_lonlat[i + 1]
            return (lon1 + t * (lon2 - lon1), lat1 + t * (lat2 - lat1))
        acc += s
    return coords_lonlat[-1]


def main() -> int:
    db = sqlite3.connect(DB)
    placeholders = ",".join("?" * len(REACH_IDS))
    rows = db.execute(
        "SELECT id, name, latitude_start, longitude_start, latitude_end, longitude_end "
        f"FROM reach WHERE id IN ({placeholders}) ORDER BY id",
        REACH_IDS,
    ).fetchall()

    for reach_id, name, plat, plon, tlat, tlon in rows:
        putin = (float(plat), float(plon))
        takeout = (float(tlat), float(tlon))
        print(f"\n=== reach {reach_id} ({name}): {putin} -> {takeout} ===")
        coords = impl.trace_reach(putin, takeout, verbose=True)  # [(lat, lon), ...]
        miles = impl.total_distance(coords)
        geom = ",".join(f"{lon:.6f} {lat:.6f}" for (lat, lon) in coords)
        coords_lonlat = [(lon, lat) for (lat, lon) in coords]
        mid = arc_length_midpoint(coords_lonlat)
        mlon, mlat = (round(mid[0], 6), round(mid[1], 6)) if mid else (None, None)
        first = coords[0]
        last = coords[-1]
        print(
            f"vertices={len(coords)} length={miles:.2f} mi "
            f"first={first[0]:.6f},{first[1]:.6f} last={last[0]:.6f},{last[1]:.6f} "
            f"midpoint={mlat},{mlon}"
        )
        db.execute(
            "UPDATE reach SET geom=?, length=?, latitude=?, longitude=? WHERE id=?",
            (geom, round(miles, 1), mlat, mlon, reach_id),
        )
    db.commit()
    db.close()
    print("\nWrote geom + length + midpoint for", REACH_IDS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
