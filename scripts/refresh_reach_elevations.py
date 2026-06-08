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
import sqlite3
import sys

import httpx

from kayak.db.safety import ProductionWriteRefused, maintenance_target_db, refuse_configured_db
from kayak.tracing.constants import M_TO_FT

EPQS_URL = "https://epqs.nationalmap.gov/v1/json"


async def fetch_elevation_m(client: httpx.AsyncClient, lon: float, lat: float) -> float | None:
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


async def gather_elevations(
    points: list[tuple[str, float, float]], concurrency: int
) -> dict[str, float | None]:
    """points: list of (tag, lon, lat). Return dict {tag: meters_or_None}."""
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(limits=limits, http2=False) as client:
        sem = asyncio.Semaphore(concurrency)

        async def one(tag: str, lon: float, lat: float) -> tuple[str, float | None]:
            async with sem:
                return tag, await fetch_elevation_m(client, lon, lat)

        tasks = [one(*p) for p in points]
        results: dict[str, float | None] = {}
        for idx, fut in enumerate(asyncio.as_completed(tasks), start=1):
            tag, meters = await fut
            results[tag] = meters
            if idx % 25 == 0:
                print(f"  ... {idx}/{len(points)} elevation lookups done", flush=True)
        return results


def _load_reaches(conn: sqlite3.Connection, reach_ids_csv: str | None) -> list[sqlite3.Row]:
    """Pull every reach with both put-in and take-out coords, optionally
    filtered to a comma-separated ID list."""
    query = """
        SELECT id, display_name,
               latitude_start, longitude_start,
               latitude_end,   longitude_end,
               elevation, elevation_lost, length, gradient
        FROM reach
        WHERE latitude_start IS NOT NULL AND longitude_start IS NOT NULL
          AND latitude_end   IS NOT NULL AND longitude_end   IS NOT NULL
    """
    params: list = []
    if reach_ids_csv:
        ids = [int(x) for x in reach_ids_csv.split(",")]
        placeholders = ",".join(["?"] * len(ids))
        query += f" AND id IN ({placeholders})"
        params = ids
    query += " ORDER BY id"
    return conn.execute(query, params).fetchall()


def _classify_reach_changes(
    reaches: list[sqlite3.Row], elevations: dict[str, float | None]
) -> tuple[list, list, list[str]]:
    """Partition each reach into update/unchanged/failed buckets.

    Returns ``(updates, failures, changed_rows)``:
      updates       list[tuple] for the UPDATE executemany
      failures      list[(rid, name, put_m, take_m)] for the failure report
      changed_rows  list[str] of preformatted lines to print
    """
    updates: list = []
    failures: list = []
    changed_rows: list[str] = []
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

        if (
            old_el == new_el
            and old_drop == new_drop
            and (old_grd == new_grd or (old_grd is None and new_grd is None))
        ):
            continue

        changed_rows.append(
            f"{rid:>5}  {(r['display_name'] or '')[:22]:<22}  "
            f"{(str(old_el) if old_el is not None else '-'):>7} -> {new_el:>7}  "
            f"{(str(old_drop) if old_drop is not None else '-'):>8} -> {new_drop:>8}  "
            f"{(str(old_grd) if old_grd is not None else '-'):>7} -> "
            f"{(str(new_grd) if new_grd is not None else '-'):>7}"
        )
        updates.append((new_el, new_drop, new_grd, rid))
    return updates, failures, changed_rows


def _print_failures(failures: list) -> None:
    if not failures:
        return
    print("\nFailed lookups (probably outside 3DEP coverage — unlikely in WA/OR/ID):")
    for rid, name, put_m, take_m in failures[:10]:
        print(f"  {rid}  {name}  put={put_m} take={take_m}")
    if len(failures) > 10:
        print(f"  ... and {len(failures) - 10} more")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--db",
        default=None,
        help="Target SQLite DB path/URL. REQUIRED for --apply (a scratch/dev copy, not "
        "the configured DB); reads/dry-run fall back to $KAYAK_DB or DATABASE_URL.",
    )
    ap.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--allow-production",
        action="store_true",
        help="Override the production-DB refusal and write the configured DB directly "
        "(reach.elevation/gradient is dataset-owned; normally write a scratch/dev copy "
        "and export_metadata the result).",
    )
    ap.add_argument(
        "--reach-ids", help="Comma-separated reach IDs to process (default: all eligible)"
    )
    ap.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args()

    # reach.elevation/elevation_lost/gradient are dataset-owned — refuse to mutate
    # the configured production DB directly (SA / AC #6). A write (--apply) must name
    # an explicit scratch/dev --db: an omitted --db resolves to the configured DB
    # (refused below), so the legacy KAYAK_DB env can't silently become an --apply
    # target. Fail fast, before any work.
    if args.apply:
        try:
            refuse_configured_db(args.db, allow_production=args.allow_production)
        except ProductionWriteRefused as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    # Resolve the DB path (handles a bare path or a sqlite:// URL). On --apply the
    # write target is the explicit --db, else the configured DB — never KAYAK_DB; a
    # read may fall back to KAYAK_DB.
    conn = sqlite3.connect(maintenance_target_db(args.db, for_write=args.apply))
    conn.row_factory = sqlite3.Row

    reaches = _load_reaches(conn, args.reach_ids)
    print(f"Scope: {len(reaches)} reach(es)")

    points: list[tuple[str, float, float]] = []
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

    updates, failures, changed_rows = _classify_reach_changes(reaches, elevations)
    for row in changed_rows:
        print(row)

    print()
    print(
        f"{len(updates)} reach(es) to update ({len(reaches) - len(updates) - len(failures)} unchanged, {len(failures)} failed)."
    )
    _print_failures(failures)

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
