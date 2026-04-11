#!/usr/bin/env python3
"""Audit gauge metadata: refresh caches, find candidates, detect data changes.

Refreshes the USGS and NWPS gauge metadata caches, then compares against the
kayak database to find:
  - New gauges near existing reaches that aren't linked to any gauge
  - New gauges on rivers that have reaches in the DB
  - Gauges that stopped providing data in the last week
  - Gauges that started providing data in the last week

Usage:
    python3 scripts/audit_gauges.py [--no-refresh] [--days 7]
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Reuse the existing fetch scripts
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

CACHE_DB = SCRIPT_DIR.parent / "Gauge-metadata-cache" / "gauges.db"
KAYAK_DB = Path.home() / "DB" / "kayak.db"


def haversine_miles(lat1, lon1, lat2, lon2):
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def refresh_caches():
    """Re-run the USGS and NWPS site fetch scripts."""
    print("=" * 60)
    print("Refreshing gauge metadata caches")
    print("=" * 60)

    from fetch_nwps_sites import main as fetch_nwps
    from fetch_usgs_sites import main as fetch_usgs

    saved_argv = sys.argv
    sys.argv = [sys.argv[0], str(CACHE_DB)]
    print("\n--- USGS sites ---")
    fetch_usgs()
    print("\n--- NWPS sites ---")
    fetch_nwps()
    sys.argv = saved_argv


def find_new_usgs_gauges(cache, kayak, active_only=True):
    """Find USGS gauges in the cache that aren't in the kayak DB."""
    # All USGS IDs currently in kayak
    known = set(
        r[0]
        for r in kayak.execute("SELECT usgs_id FROM gauge WHERE usgs_id IS NOT NULL").fetchall()
    )

    if active_only:
        # Only include sites with flow or gage data in the last 30 days
        new = cache.execute(
            "SELECT site_no, station_nm, latitude, longitude, "
            "drain_area_sq_mi, huc_cd FROM usgs_site "
            "WHERE (last_flow_date > date('now', '-30 days') "
            "    OR last_gage_date > date('now', '-30 days'))"
        ).fetchall()
    else:
        new = cache.execute(
            "SELECT site_no, station_nm, latitude, longitude, "
            "drain_area_sq_mi, huc_cd FROM usgs_site"
        ).fetchall()

    return [r for r in new if r[0] not in known]


def find_new_nwps_gauges(cache, kayak):
    """Find NWPS gauges in the cache that aren't in the kayak DB."""
    known = set()
    for col in ["nws_id", "nwsli_id", "cbtt_id"]:
        rows = kayak.execute(f"SELECT {col} FROM gauge WHERE {col} IS NOT NULL").fetchall()
        known.update(r[0] for r in rows)

    # Also check source names
    src_names = set(r[0] for r in kayak.execute("SELECT name FROM source").fetchall())
    known.update(src_names)

    new = cache.execute("SELECT lid, name, latitude, longitude, state FROM nwps_site").fetchall()

    return [r for r in new if r[0] not in known]


def find_candidates_near_reaches(new_gauges, kayak, max_dist_miles=15):
    """Find new gauges near reaches that have no gauge or a distant gauge."""
    reaches = kayak.execute("""
        SELECT r.id, r.display_name, r.name, r.river, r.gauge_id,
               r.latitude_start, r.longitude_start,
               r.latitude_end, r.longitude_end
        FROM reach r
        WHERE r.no_show = 0
          AND r.latitude_start IS NOT NULL
    """).fetchall()

    candidates = []
    for gauge in new_gauges:
        if len(gauge) == 6:
            # USGS: site_no, name, lat, lon, drain_area, huc
            gid, gname, glat, glon = gauge[0], gauge[1], gauge[2], gauge[3]
        else:
            # NWPS: lid, name, lat, lon, state
            gid, gname, glat, glon = gauge[0], gauge[1], gauge[2], gauge[3]

        if glat is None or glon is None:
            continue

        for reach in reaches:
            rid, dname, rname, _river, rgauge, slat, slon, elat, elon = reach
            label = dname or rname

            if slat is None or elat is None or slon is None or elon is None:
                continue

            # Distance to midpoint of reach
            mid_lat = (slat + elat) / 2
            mid_lon = (slon + elon) / 2
            dist = haversine_miles(glat, glon, mid_lat, mid_lon)

            if dist <= max_dist_miles:
                has_gauge = "yes" if rgauge else "NO"
                candidates.append((dist, gid, gname, rid, label, has_gauge))

    # Sort by distance, deduplicate by gauge
    candidates.sort()
    seen = set()
    unique = []
    for c in candidates:
        if c[1] not in seen:
            seen.add(c[1])
            unique.append(c)

    return unique


def check_data_status(kayak, days=7):
    """Check for gauges that stopped or started providing flow data."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    week_ago = (datetime.now(UTC) - timedelta(days=days * 2)).strftime("%Y-%m-%d %H:%M:%S")

    # Gauges that had data before the window but nothing in the last N days
    stopped = kayak.execute(
        """
        SELECT g.id, g.name, g.usgs_id,
               max(o.observed_at) AS last_obs,
               count(*) AS obs_count
        FROM gauge g
        JOIN gauge_source gs ON gs.gauge_id = g.id
        JOIN source s ON gs.source_id = s.id
        JOIN observation o ON o.source_id = s.id
        WHERE o.data_type = 'flow'
          AND o.observed_at > ?
          AND o.observed_at < ?
        GROUP BY g.id
        HAVING max(o.observed_at) < ?
    """,
        (week_ago, cutoff_str, cutoff_str),
    ).fetchall()

    # Gauges that have data in the last N days but not in the week before
    started = kayak.execute(
        """
        SELECT g.id, g.name, g.usgs_id,
               min(o.observed_at) AS first_obs,
               count(*) AS obs_count
        FROM gauge g
        JOIN gauge_source gs ON gs.gauge_id = g.id
        JOIN source s ON gs.source_id = s.id
        JOIN observation o ON o.source_id = s.id
        WHERE o.data_type = 'flow'
          AND o.observed_at > ?
        GROUP BY g.id
        HAVING min(o.observed_at) > ?
    """,
        (cutoff_str, cutoff_str),
    ).fetchall()

    # Gauges linked to reaches with no recent data at all
    stale = kayak.execute(
        """
        SELECT g.id, g.name, g.usgs_id,
               r.id AS reach_id, r.display_name AS reach_name,
               lo.observed_at, lo.data_type
        FROM reach r
        JOIN gauge g ON r.gauge_id = g.id
        JOIN gauge_source gs ON gs.gauge_id = g.id
        JOIN source s ON gs.source_id = s.id
        LEFT JOIN latest_observation lo ON lo.source_id = s.id
            AND lo.data_type = 'flow'
        WHERE r.no_show = 0
          AND (lo.observed_at IS NULL OR lo.observed_at < ?)
        GROUP BY g.id
    """,
        (cutoff_str,),
    ).fetchall()

    return stopped, started, stale


def main():
    parser = argparse.ArgumentParser(description="Audit gauge metadata")
    parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="Skip refreshing the metadata caches",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Window in days for data status checks (default: 7)",
    )
    parser.add_argument(
        "--cache-db",
        type=str,
        default=str(CACHE_DB),
        help=f"Path to gauge metadata cache (default: {CACHE_DB})",
    )
    parser.add_argument(
        "--kayak-db",
        type=str,
        default=str(KAYAK_DB),
        help=f"Path to kayak database (default: {KAYAK_DB})",
    )
    args = parser.parse_args()

    if not args.no_refresh:
        refresh_caches()

    cache = sqlite3.connect(args.cache_db)
    kayak = sqlite3.connect(args.kayak_db)

    # --- New gauges ---
    print("\n" + "=" * 60)
    print("New USGS gauges not in kayak DB")
    print("=" * 60)
    new_usgs = find_new_usgs_gauges(cache, kayak)
    print(f"Found {len(new_usgs)} USGS sites not in DB")

    print("\n" + "=" * 60)
    print("New NWPS gauges not in kayak DB")
    print("=" * 60)
    new_nwps = find_new_nwps_gauges(cache, kayak)
    print(f"Found {len(new_nwps)} NWPS sites not in DB")

    # --- Candidates near reaches ---
    print("\n" + "=" * 60)
    print("New USGS gauges within 15 miles of a reach")
    print("=" * 60)
    usgs_candidates = find_candidates_near_reaches(new_usgs, kayak)
    if usgs_candidates:
        print(f"{'Dist':>5}  {'USGS ID':<12} {'Station':<45} {'Reach':<30} {'Gauged'}")
        print("-" * 105)
        for dist, gid, gname, _rid, rlabel, has_gauge in usgs_candidates[:30]:
            print(f"{dist:>4.1f}  {gid:<12} {gname[:45]:<45} {rlabel[:30]:<30} {has_gauge}")
        if len(usgs_candidates) > 30:
            print(f"  ... and {len(usgs_candidates) - 30} more")
    else:
        print("  None found")

    print("\n" + "=" * 60)
    print("New NWPS gauges within 15 miles of a reach")
    print("=" * 60)
    nwps_candidates = find_candidates_near_reaches(new_nwps, kayak)
    if nwps_candidates:
        print(f"{'Dist':>5}  {'LID':<12} {'Name':<45} {'Reach':<30} {'Gauged'}")
        print("-" * 105)
        for dist, gid, gname, _rid, rlabel, has_gauge in nwps_candidates[:30]:
            print(f"{dist:>4.1f}  {gid:<12} {gname[:45]:<45} {rlabel[:30]:<30} {has_gauge}")
        if len(nwps_candidates) > 30:
            print(f"  ... and {len(nwps_candidates) - 30} more")
    else:
        print("  None found")

    # --- Data status ---
    print("\n" + "=" * 60)
    print(f"Gauges that STOPPED providing flow data (last {args.days} days)")
    print("=" * 60)
    stopped, started, stale = check_data_status(kayak, args.days)
    if stopped:
        for _gid, gname, usgs_id, last_obs, _count in stopped:
            print(f"  {gname:<35} (USGS {usgs_id or 'N/A':<12}) last: {last_obs}")
    else:
        print("  None")

    print("\n" + "=" * 60)
    print(f"Gauges that STARTED providing flow data (last {args.days} days)")
    print("=" * 60)
    if started:
        for _gid, gname, usgs_id, first_obs, count in started:
            print(f"  {gname:<35} (USGS {usgs_id or 'N/A':<12}) first: {first_obs}  ({count} obs)")
    else:
        print("  None")

    print("\n" + "=" * 60)
    print(f"Reach gauges with NO flow data in last {args.days} days")
    print("=" * 60)
    if stale:
        for _gid, gname, _usgs_id, _rid, rname, last_obs, _dtype in stale:
            lo = last_obs or "never"
            print(f"  {rname or '':<30} gauge={gname:<25} last flow: {lo}")
    else:
        print("  None")

    cache.close()
    kayak.close()

    print("\n" + "=" * 60)
    print("Audit complete")
    print("=" * 60)


if __name__ == "__main__":
    main()
