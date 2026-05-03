#!/usr/bin/env python3
"""Backfill gauge.huc with HUC12 codes via point-in-polygon against WBD.

Uses pyogrio's ``bbox`` filter to load only HUC12 polygons near each
gauge, so memory stays low on small VMs (the full WBD gpkg is ~600 MB
and building a tree over every polygon OOMs a 1 GB box). Scope is
limited to gauges whose current ``huc`` is NULL or shorter than 12
chars; fully-populated rows are skipped.

Usage:
    python3 scripts/backfill_gauge_huc.py          # dry-run
    python3 scripts/backfill_gauge_huc.py --apply  # write changes
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = os.environ.get("KAYAK_DB", "/home/pat/DB/kayak.db")
DEFAULT_GPKG = "/home/pat/kayak/Trace-cache/wbd.gpkg"
BBOX_PAD_DEG = 0.01  # ~1.1 km at 45°N — plenty to land inside one HUC12


def lookup_huc12(gpkg: Path, lat: float, lon: float) -> str | None:
    """Return the HUC12 code containing (lat, lon), or None if outside coverage.

    Reads only polygons whose bbox overlaps a tiny window around the
    point, then walks the (typically 1-3) candidates with a point-in-polygon
    test. Orders of magnitude less memory than loading all of WBD.
    """
    import pyogrio
    from shapely.geometry import Point

    bbox = (lon - BBOX_PAD_DEG, lat - BBOX_PAD_DEG, lon + BBOX_PAD_DEG, lat + BBOX_PAD_DEG)
    gdf = pyogrio.read_dataframe(gpkg, layer="WBDHU12", columns=["HUC12"], bbox=bbox)
    if len(gdf) == 0:
        return None
    pt = Point(lon, lat)
    for huc, geom in zip(gdf["HUC12"], gdf.geometry, strict=True):
        if geom.contains(pt):
            return str(huc)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--gpkg", default=DEFAULT_GPKG)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    gpkg = Path(args.gpkg)
    if not gpkg.exists():
        print(f"error: {gpkg} not found — run scripts/extract_wbd.sh first", file=sys.stderr)
        return 2

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    gauges = conn.execute(
        """
        SELECT id, name, latitude, longitude, huc
        FROM gauge
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
          AND (huc IS NULL OR LENGTH(huc) < 12)
        ORDER BY id
        """
    ).fetchall()

    print(f"Scope: {len(gauges)} gauge(s) with lat/lon but no/partial HUC12.\n")
    if not gauges:
        return 0

    hdr = f"{'id':>4}  {'name':<38}  {'old':>12}  {'new':>12}"
    print(hdr)
    print("-" * len(hdr))

    updates: list[tuple[str, int]] = []
    outside = 0
    for g in gauges:
        huc = lookup_huc12(gpkg, float(g["latitude"]), float(g["longitude"]))
        old = g["huc"] or "-"
        if huc is None:
            print(f"{g['id']:>4}  {(g['name'] or '')[:38]:<38}  {old:>12}  {'OUT':>12}")
            outside += 1
            continue
        if huc == g["huc"]:
            continue
        print(f"{g['id']:>4}  {(g['name'] or '')[:38]:<38}  {old:>12}  {huc:>12}")
        updates.append((huc, g["id"]))

    print(
        f"\n{len(updates)} gauge(s) to update, {outside} outside coverage, "
        f"{len(gauges) - len(updates) - outside} unchanged."
    )

    if not args.apply:
        print("\nDry-run only. Pass --apply to write changes.")
        return 0

    cur = conn.cursor()
    cur.executemany("UPDATE gauge SET huc = ? WHERE id = ?", updates)
    conn.commit()
    print(f"Applied {cur.rowcount} update(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
