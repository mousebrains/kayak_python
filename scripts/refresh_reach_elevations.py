#!/usr/bin/env python3
"""Refresh reach.elevation / elevation_lost / gradient from USGS 3DEP.

Pulls elevation at put-in and take-out for each reach with endpoint coords,
queries USGS 3DEP Point Query Service (meters, WGS84), converts to feet, and
updates the DB with a self-consistent set of values:

    elevation       = put-in elevation (ft, rounded)
    elevation_lost  = |put-in - take-out| (ft, rounded)
    gradient        = elevation_lost / length (ft/mile)

`length` is not modified (hand-picked or sourced elsewhere).
`max_gradient` is not touched (requires full profile sampling).

Usage:
    python3 scripts/refresh_reach_elevations.py [--db PATH] [--apply]
                                                [--reach-ids ID[,ID...]]
                                                [--concurrency N]
"""

import argparse
import asyncio
import os
import sqlite3
import sys

import httpx

DEFAULT_DB = os.environ.get("KAYAK_DB", "/home/pat/DB/kayak.db")
EPQS_URL = "https://epqs.nationalmap.gov/v1/json"
M_TO_FT = 3.28083989501


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
        data = r.json()
        # Response: {"location": ..., "value": "1585.303222656"} typically
        val = data.get("value")
        if val is None:
            return None
        return float(val)
    except (httpx.HTTPError, ValueError, KeyError):
        return None


async def gather_elevations(points, concurrency):
    """points: list of (tag, lon, lat). Return dict {tag: meters_or_None}."""
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(limits=limits, http2=False) as client:
        sem = asyncio.Semaphore(concurrency)

        async def one(tag, lon, lat):
            async with sem:
                return tag, await fetch_elevation_m(client, lon, lat)

        tasks = [one(*p) for p in points]
        results = {}
        for idx, fut in enumerate(asyncio.as_completed(tasks), start=1):
            tag, meters = await fut
            results[tag] = meters
            if idx % 25 == 0:
                print(f"  ... {idx}/{len(points)} elevation lookups done", flush=True)
        return results


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--reach-ids", help="Comma-separated reach IDs to process (default: all eligible)"
    )
    ap.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    query = """
        SELECT id, display_name,
               latitude_start, longitude_start,
               latitude_end,   longitude_end,
               elevation, elevation_lost, length, gradient
        FROM reach
        WHERE latitude_start IS NOT NULL AND longitude_start IS NOT NULL
          AND latitude_end   IS NOT NULL AND longitude_end   IS NOT NULL
    """
    params = []
    if args.reach_ids:
        ids = [int(x) for x in args.reach_ids.split(",")]
        placeholders = ",".join(["?"] * len(ids))
        query += f" AND id IN ({placeholders})"
        params = ids
    query += " ORDER BY id"
    reaches = conn.execute(query, params).fetchall()
    print(f"Scope: {len(reaches)} reach(es)")

    points = []
    for r in reaches:
        points.append((f"{r['id']}:put", r["longitude_start"], r["latitude_start"]))
        points.append((f"{r['id']}:take", r["longitude_end"], r["latitude_end"]))

    print(f"Fetching {len(points)} elevation points from 3DEP (concurrency={args.concurrency})...")
    elevations = asyncio.run(gather_elevations(points, args.concurrency))

    fmt_hdr = (
        f"{'id':>5}  {'display_name':<22}  "
        f"{'old_el':>7} -> {'new_el':>7}  "
        f"{'old_drop':>8} -> {'new_drop':>8}  "
        f"{'old_grd':>7} -> {'new_grd':>7}"
    )
    print()
    print(fmt_hdr)
    print("-" * len(fmt_hdr))

    updates = []
    failures = []
    for r in reaches:
        rid = r["id"]
        put_m = elevations.get(f"{rid}:put")
        take_m = elevations.get(f"{rid}:take")
        if put_m is None or take_m is None:
            failures.append((rid, r["display_name"], put_m, take_m))
            continue

        new_el = round(put_m * M_TO_FT)
        new_drop = round(abs(put_m - take_m) * M_TO_FT)
        new_grd = None
        if r["length"] and r["length"] > 0:
            new_grd = round(new_drop / r["length"], 1)

        old_el = r["elevation"]
        old_drop = r["elevation_lost"]
        old_grd = r["gradient"]

        # Skip if no change within rounding
        if (
            old_el == new_el
            and old_drop == new_drop
            and (old_grd == new_grd or (old_grd is None and new_grd is None))
        ):
            continue

        print(
            f"{rid:>5}  {(r['display_name'] or '')[:22]:<22}  "
            f"{(str(old_el) if old_el is not None else '-'):>7} -> {new_el:>7}  "
            f"{(str(old_drop) if old_drop is not None else '-'):>8} -> {new_drop:>8}  "
            f"{(str(old_grd) if old_grd is not None else '-'):>7} -> "
            f"{(str(new_grd) if new_grd is not None else '-'):>7}"
        )
        updates.append((new_el, new_drop, new_grd, rid))

    print()
    print(
        f"{len(updates)} reach(es) to update ({len(reaches) - len(updates) - len(failures)} unchanged, {len(failures)} failed)."
    )

    if failures:
        print("\nFailed lookups (probably outside 3DEP coverage — unlikely in WA/OR/ID):")
        for rid, name, put_m, take_m in failures[:10]:
            print(f"  {rid}  {name}  put={put_m} take={take_m}")
        if len(failures) > 10:
            print(f"  ... and {len(failures) - 10} more")

    if not args.apply:
        print("\nDry-run only. Pass --apply to write changes.")
        return 0

    cur = conn.cursor()
    cur.executemany(
        """
        UPDATE reach
        SET elevation = ?, elevation_lost = ?, gradient = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        updates,
    )
    conn.commit()
    print(f"Applied {cur.rowcount} update(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
