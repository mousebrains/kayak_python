#!/usr/bin/env python3
"""Backfill reach.river from NHD HR flowline GNIS_Name.

For each reach with empty river, find the dominant named flowline that
overlaps the reach geometry and propose its GNIS_Name as the river.

Default mode is dry-run; --apply walks the matches interactively
(y/n/q) and writes each accepted update as its own commit. A snapshot
of kayak.db is taken before any writes so an interrupted run leaves a
recoverable point.

Run from the repo root so the default Trace-cache paths resolve.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine.url import make_url

from kayak.config import DATABASE_URL
from kayak.db.engine import get_session
from kayak.db.models import Reach


def parse_geom(geom_str):
    """Parse 'lon lat, lon lat, ...' (the project's reach.geom format)
    into a shapely Point or LineString. Returns None on empty/invalid."""
    from shapely.geometry import LineString, Point

    if not geom_str:
        return None
    coords = []
    for pair in geom_str.split(","):
        parts = pair.strip().split()
        if len(parts) != 2:
            continue
        try:
            coords.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    if not coords:
        return None
    if len(coords) == 1:
        return Point(*coords[0])
    return LineString(coords)


def find_river(reach_geom, gpkg_path, *, buffer_deg: float = 0.0008):
    """Return (gnis_name, match_pct) for the best-matching named flowline
    overlapping the reach. match_pct is the fraction of reach length
    covered by that name (1.0 for points within tolerance, else 0.0)."""
    import geopandas as gpd

    minx, miny, maxx, maxy = reach_geom.bounds
    bbox = (
        minx - buffer_deg,
        miny - buffer_deg,
        maxx + buffer_deg,
        maxy + buffer_deg,
    )
    gdf = gpd.read_file(gpkg_path, layer="flowline", bbox=bbox)
    gdf = gdf[gdf["GNIS_Name"].notna()]
    if gdf.empty:
        return None, 0.0

    if reach_geom.geom_type == "Point":
        gdf = gdf.assign(_d=gdf.geometry.distance(reach_geom)).sort_values("_d")
        first = gdf.iloc[0]
        return first["GNIS_Name"], 1.0 if first["_d"] <= buffer_deg else 0.0

    reach_length = reach_geom.length
    if reach_length <= 0:
        return None, 0.0
    buf = reach_geom.buffer(buffer_deg)
    name_lengths: dict[str, float] = defaultdict(float)
    for _, row in gdf.iterrows():
        inter = row.geometry.intersection(buf)
        if not inter.is_empty:
            name_lengths[row["GNIS_Name"]] += inter.length
    if not name_lengths:
        return None, 0.0
    best = max(name_lengths, key=lambda k: name_lengths[k])
    return best, min(name_lengths[best], reach_length) / reach_length


def lookup_huc4(reach_geom, wbd_path: Path):
    """Spatial fallback when reach.huc is null. Returns the HUC4 string
    for the polygon containing the reach centroid, or None."""
    import geopandas as gpd

    if not wbd_path.exists():
        return None
    centroid = reach_geom.centroid
    bbox = (
        centroid.x - 0.01,
        centroid.y - 0.01,
        centroid.x + 0.01,
        centroid.y + 0.01,
    )
    gdf = gpd.read_file(wbd_path, layer="WBDHU4", bbox=bbox)
    for _, row in gdf.iterrows():
        if row.geometry.contains(centroid):
            return row.get("HUC4")
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trace-dir", type=Path, default=Path("Trace-cache/trace"))
    p.add_argument("--wbd-path", type=Path, default=Path("Trace-cache/wbd.gpkg"))
    p.add_argument(
        "--threshold",
        type=float,
        default=0.25,
        help="match_pct below this is flagged 'LOW CONFIDENCE' (default 0.25)",
    )
    p.add_argument("--reach-id", type=int, help="restrict to a single reach id")
    p.add_argument(
        "--apply",
        action="store_true",
        help="walk per-row review and write accepted updates (default: dry-run)",
    )
    args = p.parse_args()

    if args.apply:
        db_path = Path(make_url(DATABASE_URL).database)
        snap = db_path.with_name(f"{db_path.stem}.backfill_river.{datetime.now():%Y%m%d_%H%M%S}.db")
        shutil.copy2(db_path, snap)
        print(f"Snapshot written: {snap}")

    with get_session(DATABASE_URL) as session:
        q = session.query(Reach).filter((Reach.river.is_(None)) | (Reach.river == ""))
        if args.reach_id is not None:
            q = q.filter(Reach.id == args.reach_id)
        reaches = sorted(q.all(), key=lambda r: (r.huc or "", r.id))

    print(f"Reaches missing river: {len(reaches)}")
    if not reaches:
        return 0

    applied = skipped = unmatched = 0
    for r in reaches:
        geom = parse_geom(r.geom)
        if geom is None:
            print(f"[no-geom] reach {r.id}: {r.name!r}")
            unmatched += 1
            continue
        huc4 = (r.huc or "")[:4] or lookup_huc4(geom, args.wbd_path)
        if not huc4:
            print(f"[no-huc4] reach {r.id}: {r.name!r}")
            unmatched += 1
            continue
        gpkg = args.trace_dir / f"trace_{huc4}.gpkg"
        if not gpkg.exists():
            print(f"[no-gpkg] reach {r.id}: {r.name!r}: {gpkg} missing")
            unmatched += 1
            continue
        name, pct = find_river(geom, gpkg)
        if not name:
            print(f"[no-match] reach {r.id}: {r.name!r}: no GNIS in HUC{huc4}")
            unmatched += 1
            continue
        flag = "" if pct >= args.threshold else "  ← LOW CONFIDENCE"
        print()
        print(f"reach {r.id}: {r.name!r}{flag}")
        print(f"  proposed river: {name!r} ({pct * 100:.0f}% of reach)")
        if not args.apply:
            continue
        choice = input("  apply? [y]es / [n]o / [q]uit > ").strip().lower()
        if choice == "q":
            print("Stopping.")
            break
        if choice == "y":
            with get_session(DATABASE_URL) as s2:
                s2.query(Reach).filter(Reach.id == r.id).update({"river": name})
                s2.commit()
            applied += 1
        else:
            skipped += 1

    mode = "DRY RUN" if not args.apply else "APPLY"
    print(f"\n{mode} done: applied={applied} skipped={skipped} unmatched={unmatched}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
