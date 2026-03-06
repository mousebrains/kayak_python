#!/usr/bin/env python3
"""Import Dreamflows reach data and match to our database via AW IDs.

Two-phase approach:
  1. Fetch: scrape Dreamflows xlist pages per state, extract reach info
     and AW IDs. Then fetch map geometries for each reach. Save to JSON cache.
  2. Process: match cached Dreamflows reaches to our DB via aw_id, update
     geom (river track) and other metadata.

Usage:
    python3 scripts/import_dreamflows.py [--db PATH] [--dry-run]
    python3 scripts/import_dreamflows.py --fetch-only          # just build cache
    python3 scripts/import_dreamflows.py --cache-only           # process existing cache
    python3 scripts/import_dreamflows.py --state OR             # single state
    python3 scripts/import_dreamflows.py --fetch-only --no-geom # skip geometry fetch
"""

import argparse
import io
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request

# Force line-buffered stdout
if not sys.stdout.line_buffering:
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding=sys.stdout.encoding,
        errors=sys.stdout.errors, line_buffering=True,
    )

DEFAULT_CACHE = os.path.join(os.path.dirname(__file__), "..", "data", "dreamflows_cache.json")
DEFAULT_DB = os.path.join(os.path.dirname(__file__), "..", "..", "DB", "kayak.db")

# Dreamflows state pages
STATE_PAGES = {
    "AZ": "xlist-az.php",
    "CA": "xlist-ca.php",
    "CO": "xlist-co.php",
    "ID": "xlist-id.php",
    "MT": "xlist-mt.php",
    "NM": "xlist-nm.php",
    "NV": "xlist-nv.php",
    "OR": "xlist-or.php",
    "UT": "xlist-ut.php",
    "WA": "xlist-wa.php",
    "WY": "xlist-wy.php",
}

BASE_URL = "https://www.dreamflows.com"


def _log(msg):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).astimezone()
    print(f"[{now.strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch_url(url, delay=0):
    """Fetch a URL with throttling and retries.

    Returns (content, was_rate_limited). On 403, backs off exponentially.
    """
    if delay > 0:
        time.sleep(delay)
    req = urllib.request.Request(url, headers={
        "User-Agent": "kayak-import/1.0 (river levels aggregator)",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace"), False
        except urllib.error.HTTPError as e:
            if e.code == 403:
                if attempt < 2:
                    backoff = 30 * (attempt + 1)
                    _log(f"  Rate limited (403), backing off {backoff}s...")
                    time.sleep(backoff)
                else:
                    raise
            elif attempt < 2:
                _log(f"  Retry {attempt + 1} for {url}: {e}")
                time.sleep(5 * (attempt + 1))
            else:
                raise
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 2:
                _log(f"  Retry {attempt + 1} for {url}: {e}")
                time.sleep(5 * (attempt + 1))
            else:
                raise


# ---------------------------------------------------------------------------
# Phase 1a: Parse xlist pages for reach/run data and AW IDs
# ---------------------------------------------------------------------------

def parse_xlist_page(html, state):
    """Parse a Dreamflows xlist page and extract sites and runs.

    Returns list of dicts with site and run info.
    """
    sites = []
    runs = []

    # Extract gauge sites: <span id="Site769"><b>Williamson - Near Klamath Agency</b></span>
    for m in re.finditer(
        r'<span id="Site(\d+)"><b>([^<]+)</b></span>', html
    ):
        site_id = int(m.group(1))
        site_name = m.group(2).strip()
        sites.append({"id": site_id, "name": site_name})

    # Extract runs with their reach map links and AW IDs
    # Each run is in a pind1 div, with optional reach map and AW links
    # Pattern: reach description text followed by optional map and AW links

    # Find all reach map links with their surrounding context
    # Runs appear as indented text with description, then map/AW links
    run_pattern = re.compile(
        r'<img src=.?/images/pixelshim\.gif.?[^>]*>'
        r'\s*(?:<a[^>]*><img src=.?/images/querySym\.gif.?[^>]*></a>\s*)?'
        r'([^<]+?)'  # run description text
        r'(?:\s*&nbsp;)*'
        r'(.*?)'  # links section
        r'<br>',
        re.DOTALL,
    )

    for m in run_pattern.finditer(html):
        desc = m.group(1).strip()
        links_section = m.group(2)

        # Parse run description: "River Name - Section (miles, class, sources)"
        # e.g. "Klamath River - Keno Dam to Moonshine Falls (III+, 8.4 miles, AWA)"
        run_info = {
            "description": desc,
            "state": state,
            "aw_ids": [],
            "df_reach_maps": [],
        }

        # Extract AW IDs from links
        for aw_match in re.finditer(
            r'americanwhitewater\.org/content/River/detail/id/(\d+)', links_section
        ):
            run_info["aw_ids"].append(int(aw_match.group(1)))

        # Extract reach map links (single reach maps, not composite)
        for map_match in re.finditer(
            r"reachMap/index\.php\?rid=(\d+)&(?:amp;)?num=(\d+)", links_section
        ):
            rid = int(map_match.group(1))
            num = int(map_match.group(2))
            run_info["df_reach_maps"].append({"rid": rid, "num": num})

        # Extract miles and class from description
        paren_match = re.search(r'\(([^)]+)\)\s*$', desc)
        if paren_match:
            paren_text = paren_match.group(1)
            miles_m = re.search(r'([\d.]+)\s*miles?', paren_text)
            if miles_m:
                run_info["miles"] = float(miles_m.group(1))
            # Class is typically the first part before miles
            class_parts = paren_text.split(',')
            for part in class_parts:
                part = part.strip()
                if re.match(r'^[IV]+', part) or part.startswith('Class'):
                    run_info["class"] = part
                    break

        if run_info["aw_ids"] or run_info["df_reach_maps"]:
            runs.append(run_info)

    return sites, runs


# ---------------------------------------------------------------------------
# Phase 1b: Fetch map geometries
# ---------------------------------------------------------------------------

def parse_map_page(html):
    """Parse a Dreamflows reach map page and extract geometry and markers.

    Returns dict with coordinates, markers, etc.
    """
    result = {
        "segments": [],
        "markers": [],
    }

    # Extract coordinate arrays: var reachRunCoordinatesN = [ L.latLng(lat,lng), ... ];
    for seg_match in re.finditer(
        r'var reachRunCoordinates(\d+)\s*=\s*\[(.*?)\];',
        html,
        re.DOTALL,
    ):
        seg_num = int(seg_match.group(1))
        coord_text = seg_match.group(2)
        coords = []
        for c in re.finditer(r'L\.latLng\(([-\d.]+),([-\d.]+)\)', coord_text):
            coords.append([float(c.group(1)), float(c.group(2))])

        # Check if dashed (portage/road)
        style_pattern = rf"reachRunCoordinates{seg_num}.*?dashArray"
        is_dashed = bool(re.search(style_pattern, html, re.DOTALL))

        if coords:
            result["segments"].append({
                "index": seg_num,
                "coords": coords,
                "dashed": is_dashed,
            })

    # Extract markers: createMarker(layer, zoffset, type, lat, lng, hover, click)
    for mk in re.finditer(
        r"createMarker\(\w+,\s*\d+,\s*'(\w+)',\s*([-\d.]+),\s*([-\d.]+),\s*'([^']*)'",
        html,
    ):
        marker_type = mk.group(1)
        lat = float(mk.group(2))
        lng = float(mk.group(3))
        label = mk.group(4)
        result["markers"].append({
            "type": marker_type,
            "lat": lat,
            "lon": lng,
            "label": label,
        })

    return result


# ---------------------------------------------------------------------------
# Phase 1: Fetch and cache
# ---------------------------------------------------------------------------

def fetch_and_cache(cache_path, states, delay, fetch_geom, retry_errors=False):
    """Fetch Dreamflows data and save to cache."""
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            cache = json.load(f)

    cache.setdefault("sites", {})
    cache.setdefault("runs", [])
    cache.setdefault("maps", {})
    cache.setdefault("fetched_states", [])

    # Phase 1a: Fetch xlist pages
    for abbr, page in states.items():
        if abbr in cache["fetched_states"] and not fetch_geom:
            _log(f"Skipping {abbr} xlist (already cached)")
            continue

        url = f"{BASE_URL}/{page}"
        _log(f"Fetching {abbr} xlist: {url}")
        try:
            html, _ = fetch_url(url, delay=delay)
        except urllib.error.HTTPError as e:
            if e.code == 403:
                _log(f"  Rate limited on {abbr} xlist — skipping. Try again later.")
                continue
            raise

        sites, runs = parse_xlist_page(html, abbr)
        _log(f"  {abbr}: {len(sites)} sites, {len(runs)} runs with AW/map links")

        for s in sites:
            cache["sites"][str(s["id"])] = s

        # Add runs, avoiding duplicates by checking AW IDs
        existing_aw_ids = set()
        for r in cache["runs"]:
            for aw_id in r.get("aw_ids", []):
                existing_aw_ids.add(aw_id)

        added = 0
        for r in runs:
            # Check if this run's AW IDs are already in cache
            if r["aw_ids"] and all(aid in existing_aw_ids for aid in r["aw_ids"]):
                continue
            cache["runs"].append(r)
            for aid in r["aw_ids"]:
                existing_aw_ids.add(aid)
            added += 1
        _log(f"  Added {added} new runs")

        if abbr not in cache["fetched_states"]:
            cache["fetched_states"].append(abbr)

        _save_cache(cache, cache_path)

    # Phase 1b: Fetch map geometries
    if fetch_geom:
        # Clear error entries if retrying
        if retry_errors:
            error_keys = [k for k, v in cache["maps"].items() if "error" in v]
            for k in error_keys:
                del cache["maps"][k]
            if error_keys:
                _log(f"Cleared {len(error_keys)} error entries for retry")

        # Collect all unique reach maps to fetch
        maps_to_fetch = []
        for r in cache["runs"]:
            for rm in r.get("df_reach_maps", []):
                key = f"{rm['rid']}_{rm['num']}"
                if key not in cache["maps"]:
                    maps_to_fetch.append(rm)

        _log(f"Need to fetch {len(maps_to_fetch)} reach maps")

        consecutive_403s = 0
        current_delay = delay
        for i, rm in enumerate(maps_to_fetch):
            key = f"{rm['rid']}_{rm['num']}"
            url = f"{BASE_URL}/reachMap/index.php?rid={rm['rid']}&num={rm['num']}"
            _log(f"  [{i+1}/{len(maps_to_fetch)}] Fetching map rid={rm['rid']} num={rm['num']}")
            try:
                html, _ = fetch_url(url, delay=current_delay)
                map_data = parse_map_page(html)
                total_pts = sum(len(s["coords"]) for s in map_data["segments"])
                _log(f"    {len(map_data['segments'])} segments, "
                     f"{total_pts} points, "
                     f"{len(map_data['markers'])} markers")
                cache["maps"][key] = map_data
                consecutive_403s = 0
                current_delay = delay  # reset to normal delay
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    consecutive_403s += 1
                    _log(f"    Error: {e}")
                    cache["maps"][key] = {"error": str(e)}
                    if consecutive_403s >= 3:
                        _log(f"  3+ consecutive 403s — stopping geometry fetch. "
                             f"Re-run with --retry-errors later.")
                        _save_cache(cache, cache_path)
                        break
                else:
                    _log(f"    Error: {e}")
                    cache["maps"][key] = {"error": str(e)}
            except Exception as e:
                _log(f"    Error: {e}")
                cache["maps"][key] = {"error": str(e)}

            # Save periodically
            if (i + 1) % 10 == 0:
                _save_cache(cache, cache_path)
                _log(f"  Saved cache ({len(cache['maps'])} maps)")

        _save_cache(cache, cache_path)

    total_aw = sum(1 for r in cache["runs"] if r.get("aw_ids"))
    total_maps = sum(1 for k, v in cache["maps"].items() if "error" not in v)
    _log(f"Cache complete: {len(cache['runs'])} runs, "
         f"{total_aw} with AW IDs, {total_maps} maps")
    return cache


def _save_cache(cache, path):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f, indent=1)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Phase 2: Process cache against database
# ---------------------------------------------------------------------------

def process_cache(cache, db, dry_run):
    """Match Dreamflows data to our DB via AW IDs and update geometry."""
    # Build lookup: aw_id -> reach row
    rows = db.execute(
        "SELECT id, aw_id, display_name, geom FROM reach WHERE aw_id IS NOT NULL"
    ).fetchall()
    aw_to_reach = {r[1]: {"id": r[0], "display_name": r[2], "geom": r[3]} for r in rows}
    _log(f"Database has {len(aw_to_reach)} reaches with AW IDs")

    # Build lookup: aw_id -> dreamflows run with map data
    aw_to_df = {}
    for run in cache.get("runs", []):
        for aw_id in run.get("aw_ids", []):
            if aw_id not in aw_to_df:
                aw_to_df[aw_id] = run

    matched = 0
    geom_updated = 0
    geom_skipped = 0
    no_map = 0

    for aw_id, reach in aw_to_reach.items():
        df_run = aw_to_df.get(aw_id)
        if not df_run:
            continue
        matched += 1

        # Find the best map for this run
        map_data = None
        for rm in df_run.get("df_reach_maps", []):
            key = f"{rm['rid']}_{rm['num']}"
            md = cache.get("maps", {}).get(key)
            if md and "error" not in md and md.get("segments"):
                map_data = md
                break

        if not map_data:
            no_map += 1
            continue

        # Build geom string: "lon lat,lon lat,..." from all non-dashed segments
        all_coords = []
        for seg in map_data["segments"]:
            if not seg.get("dashed"):
                all_coords.extend(seg["coords"])
        # If no solid segments, use all segments
        if not all_coords:
            for seg in map_data["segments"]:
                all_coords.extend(seg["coords"])

        if not all_coords:
            no_map += 1
            continue

        # Convert to "lon lat" format (matching AW geom format)
        geom_str = ",".join(f"{c[1]} {c[0]}" for c in all_coords)

        label = "DRY-RUN" if dry_run else "UPDATE"
        if reach["geom"]:
            geom_skipped += 1
            continue

        _log(f"  [{label}] reach {reach['id']} (AW {aw_id}, {reach['display_name']}): "
             f"{len(all_coords)} points")

        if not dry_run:
            db.execute(
                "UPDATE reach SET geom = ? WHERE id = ?",
                (geom_str, reach["id"]),
            )
            geom_updated += 1

    if not dry_run:
        db.commit()

    print(f"\n=== Summary ===")
    print(f"Reaches with AW IDs in DB: {len(aw_to_reach)}")
    print(f"Matched to Dreamflows runs: {matched}")
    print(f"Geometry updated: {geom_updated}")
    print(f"Geometry skipped (already set): {geom_skipped}")
    print(f"No map data available: {no_map}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Import Dreamflows reach data and match to our DB via AW IDs"
    )
    parser.add_argument("--db", default=os.path.abspath(DEFAULT_DB),
                        help="SQLite database path")
    parser.add_argument("--cache", default=os.path.abspath(DEFAULT_CACHE),
                        help="JSON cache file path")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show matches without updating DB")
    parser.add_argument("--state",
                        help="Process only this state (e.g., OR)")
    parser.add_argument("--fetch-only", action="store_true",
                        help="Only fetch and cache; skip DB updates")
    parser.add_argument("--cache-only", action="store_true",
                        help="Only process existing cache; skip fetching")
    parser.add_argument("--no-geom", action="store_true",
                        help="Skip geometry fetching (just get xlist data)")
    parser.add_argument("--delay", type=float, default=5.0,
                        help="Seconds between requests (default: 5.0)")
    parser.add_argument("--overwrite-geom", action="store_true",
                        help="Overwrite existing geometry in DB")
    parser.add_argument("--retry-errors", action="store_true",
                        help="Clear cached errors and retry failed map fetches")
    args = parser.parse_args()

    states = STATE_PAGES
    if args.state:
        abbr = args.state.upper()
        if abbr not in STATE_PAGES:
            print(f"Unknown state: {abbr}. Valid: {', '.join(sorted(STATE_PAGES))}")
            return
        states = {abbr: STATE_PAGES[abbr]}

    # Phase 1: Fetch
    cache = None
    if not args.cache_only:
        _log(f"Delay between requests: {args.delay}s")
        cache = fetch_and_cache(
            args.cache, states, args.delay, not args.no_geom, args.retry_errors
        )

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
        if args.overwrite_geom:
            # Temporarily clear geom so process_cache will update all
            _log("--overwrite-geom: will overwrite existing geometry")
        process_cache(cache, db, args.dry_run)


if __name__ == "__main__":
    main()
