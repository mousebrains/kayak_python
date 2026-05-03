#!/usr/bin/env python3
"""Backfill gauge.state (two-letter abbreviation) for rows where it's NULL.

Two fill sources, in priority order:

1. Gauge-metadata-cache/gauges.db usgs_site table — for any gauge with
   gauge.usgs_id, copy state_cd into gauge.state.

2. reach_state link — for any remaining gauge, look at reaches with
   reach.gauge_id = gauge.id. If their states union to exactly one
   abbreviation, use it. Anything else (zero linked reaches, or multiple
   distinct states) gets reported and left NULL for manual SQL.

Usage:
    python3 scripts/backfill_gauge_state.py          # dry-run
    python3 scripts/backfill_gauge_state.py --apply  # write changes
"""

import argparse
import os
import sqlite3
import sys

DEFAULT_DB = os.environ.get("KAYAK_DB", "/home/pat/DB/kayak.db")
DEFAULT_CACHE = "/home/pat/kayak/Gauge-metadata-cache/gauges.db"

# usgs_site.state_cd is a numeric FIPS code; gauge.state stores the postal
# abbreviation to match state.abbreviation. Only the western states actually
# present in the cache need to map.
FIPS_TO_POSTAL = {
    "04": "AZ",
    "06": "CA",
    "08": "CO",
    "16": "ID",
    "20": "KS",
    "30": "MT",
    "32": "NV",
    "35": "NM",
    "41": "OR",
    "49": "UT",
    "53": "WA",
    "56": "WY",
}


def load_usgs_state_cache(cache_path):
    """Return {site_no: postal_abbrev} from the metadata cache, dropping blanks."""
    if not os.path.exists(cache_path):
        print(f"Cache not found: {cache_path} (skipping cache fill)", file=sys.stderr)
        return {}
    conn = sqlite3.connect(cache_path)
    rows = conn.execute("SELECT site_no, state_cd FROM usgs_site").fetchall()
    conn.close()
    out = {}
    for site_no, state_cd in rows:
        if not state_cd:
            continue
        postal = FIPS_TO_POSTAL.get(state_cd)
        if postal:
            out[site_no] = postal
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    cache = load_usgs_state_cache(args.cache)
    print(f"Loaded {len(cache)} USGS sites with state_cd from cache.\n")

    gauges = conn.execute(
        "SELECT id, name, usgs_id FROM gauge WHERE state IS NULL ORDER BY id"
    ).fetchall()
    print(f"{len(gauges)} gauge(s) have state IS NULL.\n")

    updates = []  # (state_abbrev, gauge_id)
    tier1 = 0
    tier2 = 0
    leftover = []  # rows that need manual assignment

    print("=== Tier 1: usgs_site.state_cd ===")
    hdr = f"{'id':>4}  {'name':<38}  {'usgs_id':<12}  {'state':<6}"
    print(hdr)
    print("-" * len(hdr))

    for g in gauges:
        state = None
        if g["usgs_id"] and g["usgs_id"] in cache:
            state = cache[g["usgs_id"]]
        if state:
            print(
                f"{g['id']:>4}  {(g['name'] or '')[:38]:<38}  "
                f"{(g['usgs_id'] or '')[:12]:<12}  {state:<6}"
            )
            updates.append((state, g["id"]))
            tier1 += 1
        else:
            leftover.append(g)

    print(f"\nTier 1 resolved: {tier1} gauge(s).\n")

    print("=== Tier 2: distinct state from linked reaches ===")
    hdr2 = f"{'id':>4}  {'name':<38}  {'state':<6}  {'note'}"
    print(hdr2)
    print("-" * 80)

    still_unresolved = []
    for g in leftover:
        rows = conn.execute(
            """
            SELECT DISTINCT s.abbreviation
            FROM reach r
            JOIN reach_state rs ON rs.reach_id = r.id
            JOIN state s        ON s.id = rs.state_id
            WHERE r.gauge_id = ?
            """,
            (g["id"],),
        ).fetchall()
        abbrevs = sorted({r["abbreviation"] for r in rows if r["abbreviation"]})
        if len(abbrevs) == 1:
            state = abbrevs[0]
            print(f"{g['id']:>4}  {(g['name'] or '')[:38]:<38}  {state:<6}  via reach.states")
            updates.append((state, g["id"]))
            tier2 += 1
        else:
            note = f"{len(abbrevs)} distinct state(s): {abbrevs}"
            print(f"{g['id']:>4}  {(g['name'] or '')[:38]:<38}  {'-':<6}  {note}")
            still_unresolved.append(g)

    print(f"\nTier 2 resolved: {tier2} gauge(s).")

    if still_unresolved:
        print("\n=== Needs manual assignment ===")
        for g in still_unresolved:
            print(
                f"  UPDATE gauge SET state = '??' WHERE id = {g['id']};  "
                f"-- {g['name']} (usgs_id={g['usgs_id']})"
            )

    total = len(updates)
    print(
        f"\nTotal updates: {total} (tier1={tier1}, tier2={tier2}, manual={len(still_unresolved)})"
    )

    if not args.apply:
        print("\nDry-run only. Pass --apply to write changes.")
        return 0

    cur = conn.cursor()
    cur.executemany(
        "UPDATE gauge SET state = ? WHERE id = ? AND state IS NULL",
        updates,
    )
    conn.commit()
    print(f"Applied {total} update(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
