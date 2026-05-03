#!/usr/bin/env python3
"""Backfill gauge.drainage_area and gauge.elevation from cached + live sources.

Two fill sources, in priority order:

1. Gauge-metadata-cache/gauges.db usgs_site table — for any gauge with
   gauge.usgs_id, copy drain_area_sq_mi → drainage_area and altitude_ft →
   elevation where the kayak DB is NULL.

2. USGS 3DEP point query — for any remaining gauge that still has no
   elevation but has latitude/longitude, fetch meters and convert to feet.
   Mirrors scripts/refresh_reach_elevations.py.

Drainage area for NWS-only or calc/merge virtual gauges is NOT filled (no
natural single source). Those stay NULL and rely on the sort's NULLS-LAST
fallback.

Usage:
    python3 scripts/backfill_gauge_metadata.py          # dry-run
    python3 scripts/backfill_gauge_metadata.py --apply  # write changes
    python3 scripts/backfill_gauge_metadata.py --apply --cache-only  # skip 3DEP
"""

import argparse
import asyncio
import os
import sqlite3
import sys

import httpx

DEFAULT_DB = os.environ.get("KAYAK_DB", "/home/pat/DB/kayak.db")
DEFAULT_CACHE = "/home/pat/kayak/Gauge-metadata-cache/gauges.db"
EPQS_URL = "https://epqs.nationalmap.gov/v1/json"
M_TO_FT = 3.28083989501


def load_usgs_cache(cache_path):
    """Return {site_no: (drain_area_sq_mi, altitude_ft)} from the metadata cache."""
    if not os.path.exists(cache_path):
        print(f"Cache not found: {cache_path} (skipping cache fill)", file=sys.stderr)
        return {}
    conn = sqlite3.connect(cache_path)
    rows = conn.execute("SELECT site_no, drain_area_sq_mi, altitude_ft FROM usgs_site").fetchall()
    conn.close()
    return {site_no: (da, alt) for site_no, da, alt in rows}


async def fetch_elevation_m(client, lon, lat):
    """Return elevation in meters at (lat, lon) from USGS 3DEP, or None."""
    params = {
        "x": f"{lon}",
        "y": f"{lat}",
        "wkid": "4326",
        "units": "Meters",
        "includeDate": "False",
    }
    try:
        r = await client.get(EPQS_URL, params=params, timeout=30.0)
        if r.status_code != 200:
            return None
        val = r.json().get("value")
        if val is None:
            return None
        return float(val)
    except (httpx.HTTPError, ValueError, KeyError):
        return None


async def gather_elevations(points, concurrency):
    """points: list of (gid, lon, lat). Return {gid: meters_or_None}."""
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(limits=limits, http2=False) as client:
        sem = asyncio.Semaphore(concurrency)

        async def one(gid, lon, lat):
            async with sem:
                return gid, await fetch_elevation_m(client, lon, lat)

        tasks = [one(*p) for p in points]
        results = {}
        for idx, fut in enumerate(asyncio.as_completed(tasks), start=1):
            gid, meters = await fut
            results[gid] = meters
            if idx % 10 == 0:
                print(f"  ... {idx}/{len(points)} elevation lookups done", flush=True)
        return results


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--cache-only",
        action="store_true",
        help="Skip 3DEP DEM step; only sync from the metadata cache.",
    )
    ap.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    cache = load_usgs_cache(args.cache)
    print(f"Loaded {len(cache)} USGS sites from cache.\n")

    gauges = conn.execute(
        """
        SELECT id, name, usgs_id, latitude, longitude,
               drainage_area, elevation
        FROM gauge
        WHERE drainage_area IS NULL OR elevation IS NULL
        ORDER BY id
        """
    ).fetchall()

    # Phase 1: cache fill
    cache_updates = []  # (new_da, new_elev, gid)
    need_dem = []  # (gid, lon, lat, name) — still missing elevation after cache

    print("=== Phase 1: sync from Gauge-metadata-cache/gauges.db ===")
    hdr = f"{'id':>4}  {'name':<38}  {'da old->new':>22}  {'elev old->new':>22}"
    print(hdr)
    print("-" * len(hdr))

    for g in gauges:
        new_da = g["drainage_area"]
        new_elev = g["elevation"]

        if g["usgs_id"] and g["usgs_id"] in cache:
            cda, calt = cache[g["usgs_id"]]
            if new_da is None and cda is not None:
                new_da = float(cda)
            if new_elev is None and calt is not None:
                new_elev = float(calt)

        changed = new_da != g["drainage_area"] or new_elev != g["elevation"]
        if changed:
            print(
                f"{g['id']:>4}  {(g['name'] or '')[:38]:<38}  "
                f"{(str(g['drainage_area']) if g['drainage_area'] is not None else '-'):>9}"
                f" -> {(str(new_da) if new_da is not None else '-'):<9}  "
                f"{(str(g['elevation']) if g['elevation'] is not None else '-'):>9}"
                f" -> {(str(new_elev) if new_elev is not None else '-'):<9}"
            )
            cache_updates.append((new_da, new_elev, g["id"]))

        if new_elev is None and g["latitude"] is not None and g["longitude"] is not None:
            need_dem.append((g["id"], float(g["longitude"]), float(g["latitude"]), g["name"]))

    print(f"\nCache fill: {len(cache_updates)} gauge(s) would change.")

    # Phase 2: 3DEP for remaining missing elevations
    dem_updates = []  # (elev_ft, gid)
    if not args.cache_only and need_dem:
        print(
            f"\n=== Phase 2: 3DEP DEM for {len(need_dem)} gauge(s) "
            f"missing elevation but with lat/lon ==="
        )
        points = [(gid, lon, lat) for gid, lon, lat, _name in need_dem]
        results = asyncio.run(gather_elevations(points, args.concurrency))

        hdr2 = f"{'id':>4}  {'name':<38}  {'elev (ft)':>10}"
        print(hdr2)
        print("-" * len(hdr2))
        for gid, _lon, _lat, name in need_dem:
            meters = results.get(gid)
            if meters is None:
                print(f"{gid:>4}  {(name or '')[:38]:<38}  {'FAIL':>10}")
                continue
            ft = round(meters * M_TO_FT, 1)
            print(f"{gid:>4}  {(name or '')[:38]:<38}  {ft:>10}")
            dem_updates.append((ft, gid))

        print(f"\n3DEP fill: {len(dem_updates)} gauge(s) got elevation.")
    elif args.cache_only and need_dem:
        print(f"\n(Skipping 3DEP — {len(need_dem)} gauge(s) would be eligible.)")

    total = len(cache_updates) + len(dem_updates)
    print(f"\nTotal updates: {total} (cache={len(cache_updates)}, dem={len(dem_updates)})")

    if not args.apply:
        print("\nDry-run only. Pass --apply to write changes.")
        return 0

    cur = conn.cursor()
    if cache_updates:
        # COALESCE(existing, new) — never clobber a populated value with NULL
        # (e.g. when the cache has DA but no altitude for a USGS site).
        cur.executemany(
            """
            UPDATE gauge
            SET drainage_area = COALESCE(drainage_area, ?),
                elevation     = COALESCE(elevation, ?)
            WHERE id = ?
            """,
            cache_updates,
        )
    if dem_updates:
        cur.executemany(
            "UPDATE gauge SET elevation = ? WHERE id = ? AND elevation IS NULL",
            dem_updates,
        )
    conn.commit()
    print(f"Applied {total} update(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
