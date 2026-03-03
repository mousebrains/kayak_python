#!/usr/bin/env python3
"""Match American Whitewater reaches to local reaches via shared gauge IDs.

Two-phase approach:
  1. Fetch: paginate AW reaches per state, batch-query their gauges, save to
     a JSON cache file.  Throttled to ~60 minutes for ~2,000 reaches.
  2. Process: load cache, match gauge source_ids to our source names, update
     reach.aw_id, put-in/take-out coordinates, and backfill missing gauge
     identifiers (usgs_id, cbtt_id).

Usage:
    python3 scripts/match_aw_reaches.py [--db PATH] [--dry-run] [--state OR]
    python3 scripts/match_aw_reaches.py --fetch-only          # just build cache
    python3 scripts/match_aw_reaches.py --cache-only           # process existing cache
"""

import argparse
from datetime import datetime, timedelta, timezone
import io
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

# Force line-buffered stdout so progress is visible when piped to a file
if not sys.stdout.line_buffering:
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding=sys.stdout.encoding,
        errors=sys.stdout.errors, line_buffering=True,
    )

GRAPHQL_URL = "https://www.americanwhitewater.org/graphql"
DEFAULT_CACHE = os.path.join(os.path.dirname(__file__), "..", "data", "aw_reaches.json")
BATCH_SIZE = 20  # reaches per batched gauge query

# Map our state abbreviations to AW state codes
STATE_MAP = {
    "AZ": "USA-ARZ",
    "CA": "USA-CAL",
    "CO": "USA-COL",
    "ID": "USA-IDA",
    "KS": "USA-KAN",
    "MT": "USA-MNT",
    "NV": "USA-NEV",
    "NM": "USA-NME",
    "OR": "USA-ORE",
    "UT": "USA-UTA",
    "WA": "USA-WSH",
    "WY": "USA-WYM",
}

# Map AW gauge source names to our gauge table columns
AW_SOURCE_TO_GAUGE_COL = {
    "usgs": "usgs_id",
    "nwrfc": "cbtt_id",
}


def graphql(query, variables=None):
    """Execute a GraphQL query against the AW API."""
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
    if "errors" in result:
        raise RuntimeError(f"GraphQL errors: {result['errors']}")
    return result


def fetch_reaches_page(aw_state, page):
    """Fetch one page of reaches for an AW state code."""
    result = graphql(
        """
        query ($states: [String], $first: Int!, $page: Int!) {
            reaches(states: $states, first: $first, page: $page) {
                data {
                    id river section class
                    plat plon tlat tlon
                    length avggradient maxgradient
                }
                paginatorInfo { hasMorePages }
            }
        }
        """,
        {"states": [aw_state], "first": 100, "page": page},
    )
    data = result.get("data", {}).get("reaches", {})
    return data.get("data", []), data.get("paginatorInfo", {}).get("hasMorePages", False)


def fetch_gauges_batch(reach_ids):
    """Fetch gauge info for multiple reaches in one request using aliases.

    Returns dict of reach_id -> list of gauge dicts.
    """
    if not reach_ids:
        return {}
    # Build aliased query fragments
    fragments = []
    for rid in reach_ids:
        fragments.append(
            f'r{rid}: getGaugeInformationForReachID(id: "{rid}") {{\n'
            f"  gauges {{ gauge {{ source source_id name }} }}\n"
            f"}}"
        )
    query = "{\n" + "\n".join(fragments) + "\n}"
    result = graphql(query)
    out = {}
    for rid in reach_ids:
        info = result.get("data", {}).get(f"r{rid}")
        gauges = []
        if info:
            for item in info.get("gauges") or []:
                g = item.get("gauge")
                if g and g.get("source") and g.get("source_id"):
                    gauges.append({
                        "source": g["source"].lower(),
                        "source_id": g["source_id"],
                        "name": g.get("name") or "",
                    })
        out[rid] = gauges
    return out


# ---------------------------------------------------------------------------
# Phase 1: Fetch from AW API and save cache
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc)


def _fmt(dt):
    """Format a datetime as local HH:MM:SS."""
    local = dt.astimezone()
    return local.strftime("%H:%M:%S")


def _log(msg):
    print(f"[{_fmt(_now())}] {msg}", flush=True)


def fetch_and_cache(cache_path, states_to_process, delay):
    """Fetch all AW reaches and their gauges, save to JSON cache."""
    # Load existing cache for resumption
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            cache = json.load(f)
    if "reaches" not in cache:
        cache["reaches"] = {}

    requests_made = 0
    start_time = _now()

    # First pass: count total work to estimate completion
    _log("Counting reaches per state...")

    for abbr, aw_code in states_to_process.items():
        _log(f"--- Fetching {abbr} ({aw_code}) ---")

        # Paginate reaches
        all_reaches = []
        page = 1
        while True:
            time.sleep(delay)
            reaches, has_more = fetch_reaches_page(aw_code, page)
            requests_made += 1
            all_reaches.extend(reaches)
            _log(f"  Page {page}: {len(reaches)} reaches" +
                 (" (last)" if not has_more else ""))
            if not has_more:
                break
            page += 1

        _log(f"  Total: {len(all_reaches)} reaches for {abbr}")

        # Collect reach IDs that need gauge fetching
        needs_gauges = []
        for r in all_reaches:
            rid = str(r["id"])
            cache["reaches"].setdefault(rid, {})
            cache["reaches"][rid].update({
                "id": r["id"],
                "river": r.get("river"),
                "section": r.get("section"),
                "class": r.get("class"),
                "plat": r.get("plat"),
                "plon": r.get("plon"),
                "tlat": r.get("tlat"),
                "tlon": r.get("tlon"),
                "length": r.get("length"),
                "avggradient": r.get("avggradient"),
                "maxgradient": r.get("maxgradient"),
                "state": abbr,
            })
            if "gauges" not in cache["reaches"][rid]:
                needs_gauges.append(r["id"])

        cached_count = len(all_reaches) - len(needs_gauges)
        _log(f"  Need gauge fetch for {len(needs_gauges)} reaches "
             f"({cached_count} cached)")

        # Batch gauge queries
        total_batches = (len(needs_gauges) + BATCH_SIZE - 1) // BATCH_SIZE
        for i in range(0, len(needs_gauges), BATCH_SIZE):
            batch = needs_gauges[i : i + BATCH_SIZE]
            batch_num = i // BATCH_SIZE + 1
            next_at = _now() + timedelta(seconds=delay)
            time.sleep(delay)
            try:
                gauges_map = fetch_gauges_batch(batch)
                requests_made += 1
            except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as e:
                _log(f"  Error fetching gauge batch: {e}")
                _save_cache(cache, cache_path)
                continue
            for rid, gauges in gauges_map.items():
                cache["reaches"][str(rid)]["gauges"] = gauges
            done = min(i + BATCH_SIZE, len(needs_gauges))
            elapsed = (_now() - start_time).total_seconds()
            rate = requests_made / elapsed if elapsed > 0 else 0
            if batch_num < total_batches:
                _log(f"  Gauges: {done}/{len(needs_gauges)} "
                     f"(batch {batch_num}/{total_batches}, "
                     f"next request ~{_fmt(next_at + timedelta(seconds=delay))})")
            else:
                _log(f"  Gauges: {done}/{len(needs_gauges)} (done)")

        # Save after each state
        _save_cache(cache, cache_path)
        _log(f"  Saved cache ({len(cache['reaches'])} total reaches)")

    elapsed = (_now() - start_time).total_seconds()
    _log(f"Fetch complete. {requests_made} requests in {elapsed/60:.1f} min.")
    _log(f"Cache: {len(cache['reaches'])} reaches saved to {cache_path}")
    return cache


def _save_cache(cache, cache_path):
    tmp = cache_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f, indent=1)
    os.replace(tmp, cache_path)


# ---------------------------------------------------------------------------
# Phase 2: Process cache against database
# ---------------------------------------------------------------------------

def build_source_lookup(db):
    """Build a mapping from source name -> set of (reach_id, gauge_id)."""
    rows = db.execute(
        """
        SELECT src.name, r.id, r.gauge_id
        FROM source src
        JOIN gauge_source gs ON gs.source_id = src.id
        JOIN reach r ON r.gauge_id = gs.gauge_id
        """
    ).fetchall()
    lookup = {}
    for source_name, reach_id, gauge_id in rows:
        lookup.setdefault(source_name, set()).add((reach_id, gauge_id))
    return lookup


def build_gauge_ids(db):
    """Load current gauge identifier columns for backfill detection."""
    rows = db.execute("SELECT id, usgs_id, cbtt_id FROM gauge").fetchall()
    return {r[0]: {"usgs_id": r[1], "cbtt_id": r[2]} for r in rows}


def match_source_id(aw_source, aw_source_id, lookup):
    """Try to match an AW gauge source_id to our source names.

    Returns set of (reach_id, gauge_id) tuples.
    """
    matched = set()

    # Exact match
    if aw_source_id in lookup:
        matched.update(lookup[aw_source_id])

    # For NWRFC/USBR-style IDs, try stripping trailing digits
    # e.g. BUMO3 -> BUMO
    if not matched and aw_source in ("nwrfc", "usbr"):
        stripped = aw_source_id.rstrip("0123456789")
        if stripped and stripped != aw_source_id and stripped in lookup:
            matched.update(lookup[stripped])

    # Try adding common suffixes for short NWRFC codes
    if not matched and aw_source in ("nwrfc",):
        for suffix in ("O3", "I", "W3"):
            candidate = aw_source_id + suffix
            if candidate in lookup:
                matched.update(lookup[candidate])

    return matched


def process_cache(cache, db, dry_run):
    """Match cached AW reaches to DB reaches and apply updates."""
    lookup = build_source_lookup(db)
    gauge_ids = build_gauge_ids(db)
    print(f"Built lookup: {len(lookup)} source names -> reaches")

    total_matched = 0
    total_updated = 0
    total_reaches = 0
    total_conflicts = 0
    gauge_ids_filled = 0

    for rid_str, reach in cache.get("reaches", {}).items():
        total_reaches += 1
        aw_gauges = reach.get("gauges", [])
        if not aw_gauges:
            continue

        # Collect all (reach_id, gauge_id) matched through any gauge
        matched = set()
        for g in aw_gauges:
            matched.update(
                match_source_id(g["source"], g["source_id"], lookup)
            )
        if not matched:
            continue

        total_matched += 1
        aw_reach_id = int(reach["id"])
        plat = reach.get("plat")
        plon = reach.get("plon")
        tlat = reach.get("tlat")
        tlon = reach.get("tlon")
        aw_name = f"{reach.get('river', '')} - {reach.get('section', '')}"

        matched_reaches = {r for r, g in matched}
        if len(matched_reaches) > 1:
            total_conflicts += 1

        for db_reach_id, gauge_id in matched:
            label = "DRY-RUN" if dry_run else "UPDATE"
            print(f"  [{label}] reach {db_reach_id} -> AW {aw_reach_id} ({aw_name})")

            if not dry_run:
                updates = ["aw_id = ?"]
                params = [aw_reach_id]
                if plat and plon:
                    updates += ["latitude_start = ?", "longitude_start = ?"]
                    params += [float(plat), float(plon)]
                if tlat and tlon:
                    updates += ["latitude_end = ?", "longitude_end = ?"]
                    params += [float(tlat), float(tlon)]
                params.append(db_reach_id)
                db.execute(
                    f"UPDATE reach SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
                total_updated += 1

            # Backfill missing gauge identifiers
            if gauge_id in gauge_ids:
                current = gauge_ids[gauge_id]
                for g in aw_gauges:
                    col = AW_SOURCE_TO_GAUGE_COL.get(g["source"])
                    if not col or current.get(col):
                        continue
                    print(f"    [{label}] gauge {gauge_id}: set {col} = {g['source_id']}")
                    if not dry_run:
                        db.execute(
                            f"UPDATE gauge SET {col} = ? WHERE id = ?",
                            (g["source_id"], gauge_id),
                        )
                        current[col] = g["source_id"]
                    gauge_ids_filled += 1

    if not dry_run:
        db.commit()

    print(f"\n=== Summary ===")
    print(f"Total AW reaches in cache: {total_reaches}")
    print(f"Reaches matched to our reaches: {total_matched}")
    if not dry_run:
        print(f"Reaches updated: {total_updated}")
    print(f"Multi-reach matches (conflicts): {total_conflicts}")
    print(f"Gauge identifiers backfilled: {gauge_ids_filled}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Match AW reaches to local reaches via shared gauge IDs"
    )
    parser.add_argument("--db", default="/home/pat/DB/kayak.db",
                        help="SQLite database path")
    parser.add_argument("--cache", default=os.path.abspath(DEFAULT_CACHE),
                        help="JSON cache file path")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show matches without updating DB")
    parser.add_argument("--state",
                        help="Process only this state abbreviation (e.g., OR)")
    parser.add_argument("--fetch-only", action="store_true",
                        help="Only fetch from AW API and save cache; skip DB updates")
    parser.add_argument("--cache-only", action="store_true",
                        help="Only process existing cache; skip API fetching")
    parser.add_argument("--delay", type=float, default=None,
                        help="Seconds between API requests (default: auto for ~60 min)")
    args = parser.parse_args()

    states_to_process = STATE_MAP
    if args.state:
        abbr = args.state.upper()
        if abbr not in STATE_MAP:
            print(f"Unknown state: {abbr}. Valid: {', '.join(sorted(STATE_MAP))}")
            return
        states_to_process = {abbr: STATE_MAP[abbr]}

    # Phase 1: Fetch
    cache = None
    if not args.cache_only:
        # Estimate request count for delay calculation:
        # ~2071 reaches / 20 per batch = ~104 batch requests + ~21 pagination pages
        # ≈ 125 requests for all states.  Target 60 min = 3600s => ~29s per request.
        # For a single state, scale proportionally.
        if args.delay is not None:
            delay = args.delay
        else:
            # ~125 requests for all 12 states, scale down for --state
            est_requests = 125 if not args.state else 15
            delay = 3600.0 / est_requests
        print(f"Delay between requests: {delay:.1f}s")
        cache = fetch_and_cache(args.cache, states_to_process, delay)

    # Phase 2: Process
    if not args.fetch_only:
        if cache is None:
            if not os.path.exists(args.cache):
                print(f"Cache file not found: {args.cache}")
                print("Run without --cache-only first to fetch data.")
                return
            with open(args.cache) as f:
                cache = json.load(f)
        db = sqlite3.connect(args.db)
        process_cache(cache, db, args.dry_run)


if __name__ == "__main__":
    main()
