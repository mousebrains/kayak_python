#!/usr/bin/env python3
"""Import Dreamflows reach data and match to our database via AW IDs.

Two-phase approach:
  1. Fetch: scrape Dreamflows xlist pages per state, extract reach info
     and AW IDs. Then fetch map geometries for each reach. Save to the
     dreamflows_* tables in the gauge metadata cache DB.
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

DEFAULT_METADATA_DB = os.path.join(
    os.path.dirname(__file__), "..", "Gauge-metadata-cache", "gauges.db"
)
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

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS dreamflows_site (
    id            INTEGER PRIMARY KEY,
    name          TEXT
);
CREATE TABLE IF NOT EXISTS dreamflows_run (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    description   TEXT,
    state         TEXT,
    aw_ids        TEXT,
    site_id       INTEGER,
    map_num       INTEGER
);
CREATE TABLE IF NOT EXISTS dreamflows_map (
    key           TEXT PRIMARY KEY,
    site_id       INTEGER,
    map_num       INTEGER,
    segments      TEXT,
    markers       TEXT,
    error         TEXT
);
CREATE TABLE IF NOT EXISTS dreamflows_fetched_state (
    abbreviation  TEXT PRIMARY KEY
);
"""


def _log(msg):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).astimezone()
    print(f"[{now.strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch_url(url, delay=0):
    """Fetch a URL with throttling and retries."""
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
    """Parse a Dreamflows xlist page and extract sites and runs."""
    sites = []
    runs = []

    for m in re.finditer(
        r'<span id="Site(\d+)"><b>([^<]+)</b></span>', html
    ):
        site_id = int(m.group(1))
        site_name = m.group(2).strip()
        sites.append({"id": site_id, "name": site_name})

    run_pattern = re.compile(
        r'<img src=.?/images/pixelshim\.gif.?[^>]*>'
        r'\s*(?:<a[^>]*><img src=.?/images/querySym\.gif.?[^>]*></a>\s*)?'
        r'([^<]+?)'
        r'(?:\s*&nbsp;)*'
        r'(.*?)'
        r'<br>',
        re.DOTALL,
    )

    for m in run_pattern.finditer(html):
        desc = m.group(1).strip()
        links_section = m.group(2)

        run_info = {
            "description": desc,
            "state": state,
            "aw_ids": [],
            "df_reach_maps": [],
        }

        for aw_match in re.finditer(
            r'americanwhitewater\.org/content/River/detail/id/(\d+)', links_section
        ):
            run_info["aw_ids"].append(int(aw_match.group(1)))

        for map_match in re.finditer(
            r"reachMap/index\.php\?rid=(\d+)&(?:amp;)?num=(\d+)", links_section
        ):
            rid = int(map_match.group(1))
            num = int(map_match.group(2))
            run_info["df_reach_maps"].append({"rid": rid, "num": num})

        if run_info["aw_ids"] or run_info["df_reach_maps"]:
            runs.append(run_info)

    return sites, runs


# ---------------------------------------------------------------------------
# Phase 1b: Fetch map geometries
# ---------------------------------------------------------------------------

def parse_map_page(html):
    """Parse a Dreamflows reach map page and extract geometry and markers."""
    result = {
        "segments": [],
        "markers": [],
    }

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

        style_pattern = rf"reachRunCoordinates{seg_num}.*?dashArray"
        is_dashed = bool(re.search(style_pattern, html, re.DOTALL))

        if coords:
            result["segments"].append({
                "index": seg_num,
                "coords": coords,
                "dashed": is_dashed,
            })

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
# Phase 1: Fetch and store to metadata DB
# ---------------------------------------------------------------------------

def fetch_and_store(meta_db, states, delay, fetch_geom, retry_errors=False):
    """Fetch Dreamflows data and save to metadata DB."""
    meta_db.executescript(CREATE_TABLES)

    # Load already-fetched states
    fetched_states = {
        r[0] for r in meta_db.execute("SELECT abbreviation FROM dreamflows_fetched_state")
    }
    # Load existing AW IDs to avoid duplicate runs
    existing_aw_ids = set()
    for row in meta_db.execute("SELECT aw_ids FROM dreamflows_run"):
        if row[0]:
            for aid in json.loads(row[0]):
                existing_aw_ids.add(aid)

    # Phase 1a: Fetch xlist pages
    for abbr, page in states.items():
        if abbr in fetched_states and not fetch_geom:
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
            meta_db.execute(
                "INSERT OR REPLACE INTO dreamflows_site (id, name) VALUES (?, ?)",
                (s["id"], s["name"]),
            )

        added = 0
        for r in runs:
            if r["aw_ids"] and all(aid in existing_aw_ids for aid in r["aw_ids"]):
                continue
            aw_ids_json = json.dumps(r.get("aw_ids", []))
            for dm in r.get("df_reach_maps", []):
                meta_db.execute(
                    "INSERT INTO dreamflows_run (description, state, aw_ids, site_id, map_num) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (r.get("description"), r.get("state"), aw_ids_json,
                     dm.get("rid"), dm.get("num")),
                )
            # If run has no reach maps but has AW IDs, still store it
            if not r.get("df_reach_maps") and r.get("aw_ids"):
                meta_db.execute(
                    "INSERT INTO dreamflows_run (description, state, aw_ids) "
                    "VALUES (?, ?, ?)",
                    (r.get("description"), r.get("state"), aw_ids_json),
                )
            for aid in r["aw_ids"]:
                existing_aw_ids.add(aid)
            added += 1
        _log(f"  Added {added} new runs")

        meta_db.execute(
            "INSERT OR IGNORE INTO dreamflows_fetched_state (abbreviation) VALUES (?)",
            (abbr,),
        )
        meta_db.commit()

    # Phase 1b: Fetch map geometries
    if fetch_geom:
        if retry_errors:
            deleted = meta_db.execute(
                "DELETE FROM dreamflows_map WHERE error IS NOT NULL"
            ).rowcount
            meta_db.commit()
            if deleted:
                _log(f"Cleared {deleted} error entries for retry")

        # Collect all unique reach maps to fetch
        maps_to_fetch = []
        existing_keys = {
            r[0] for r in meta_db.execute("SELECT key FROM dreamflows_map")
        }
        for row in meta_db.execute("SELECT site_id, map_num FROM dreamflows_run WHERE site_id IS NOT NULL AND map_num IS NOT NULL"):
            key = f"{row[0]}_{row[1]}"
            if key not in existing_keys:
                maps_to_fetch.append({"rid": row[0], "num": row[1]})
                existing_keys.add(key)  # avoid duplicates in the fetch list

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
                meta_db.execute(
                    "INSERT OR REPLACE INTO dreamflows_map "
                    "(key, site_id, map_num, segments, markers) VALUES (?, ?, ?, ?, ?)",
                    (key, rm["rid"], rm["num"],
                     json.dumps(map_data["segments"]),
                     json.dumps(map_data["markers"])),
                )
                consecutive_403s = 0
                current_delay = delay
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    consecutive_403s += 1
                _log(f"    Error: {e}")
                meta_db.execute(
                    "INSERT OR REPLACE INTO dreamflows_map "
                    "(key, site_id, map_num, error) VALUES (?, ?, ?, ?)",
                    (key, rm["rid"], rm["num"], str(e)),
                )
                if consecutive_403s >= 3:
                    _log(f"  3+ consecutive 403s — stopping geometry fetch. "
                         f"Re-run with --retry-errors later.")
                    meta_db.commit()
                    break
            except Exception as e:
                _log(f"    Error: {e}")
                meta_db.execute(
                    "INSERT OR REPLACE INTO dreamflows_map "
                    "(key, site_id, map_num, error) VALUES (?, ?, ?, ?)",
                    (key, rm["rid"], rm["num"], str(e)),
                )

            if (i + 1) % 10 == 0:
                meta_db.commit()
                total_maps = meta_db.execute(
                    "SELECT COUNT(*) FROM dreamflows_map WHERE error IS NULL"
                ).fetchone()[0]
                _log(f"  Saved ({total_maps} maps)")

        meta_db.commit()

    total_runs = meta_db.execute("SELECT COUNT(*) FROM dreamflows_run").fetchone()[0]
    total_aw = meta_db.execute(
        "SELECT COUNT(*) FROM dreamflows_run WHERE aw_ids IS NOT NULL AND aw_ids != '[]'"
    ).fetchone()[0]
    total_maps = meta_db.execute(
        "SELECT COUNT(*) FROM dreamflows_map WHERE error IS NULL"
    ).fetchone()[0]
    _log(f"Cache complete: {total_runs} runs, {total_aw} with AW IDs, {total_maps} maps")


# ---------------------------------------------------------------------------
# Phase 2: Process cached data against kayak database
# ---------------------------------------------------------------------------

def process_cached(meta_db, db, dry_run):
    """Match Dreamflows data to our DB via AW IDs and update geometry."""
    # Build lookup: aw_id -> reach row
    rows = db.execute(
        "SELECT id, aw_id, display_name, geom FROM reach WHERE aw_id IS NOT NULL"
    ).fetchall()
    aw_to_reach = {r[1]: {"id": r[0], "display_name": r[2], "geom": r[3]} for r in rows}
    _log(f"Database has {len(aw_to_reach)} reaches with AW IDs")

    # Build lookup: aw_id -> dreamflows run with map data
    aw_to_df = {}
    for row in meta_db.execute(
        "SELECT description, state, aw_ids, site_id, map_num FROM dreamflows_run"
    ):
        aw_ids = json.loads(row[2]) if row[2] else []
        run = {
            "description": row[0],
            "state": row[1],
            "aw_ids": aw_ids,
            "df_reach_maps": [],
        }
        if row[3] is not None and row[4] is not None:
            run["df_reach_maps"].append({"rid": row[3], "num": row[4]})
        for aw_id in aw_ids:
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
            md_row = meta_db.execute(
                "SELECT segments, markers FROM dreamflows_map WHERE key = ? AND error IS NULL",
                (key,),
            ).fetchone()
            if md_row and md_row[0]:
                map_data = {
                    "segments": json.loads(md_row[0]),
                    "markers": json.loads(md_row[1]) if md_row[1] else [],
                }
                break

        if not map_data:
            no_map += 1
            continue

        # Build geom string from all non-dashed segments
        all_coords = []
        for seg in map_data["segments"]:
            if not seg.get("dashed"):
                all_coords.extend(seg["coords"])
        if not all_coords:
            for seg in map_data["segments"]:
                all_coords.extend(seg["coords"])

        if not all_coords:
            no_map += 1
            continue

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
    parser.add_argument("--metadata-db", default=os.path.abspath(DEFAULT_METADATA_DB),
                        help="Gauge metadata cache DB path")
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

    meta_db = sqlite3.connect(args.metadata_db)

    # Phase 1: Fetch
    if not args.cache_only:
        _log(f"Delay between requests: {args.delay}s")
        fetch_and_store(meta_db, states, args.delay, not args.no_geom, args.retry_errors)

    # Phase 2: Process
    if not args.fetch_only:
        total = meta_db.execute(
            "SELECT COUNT(*) FROM dreamflows_run"
        ).fetchone()[0]
        if total == 0:
            print("No Dreamflows data in metadata DB.")
            print("Run without --cache-only first to fetch data.")
            meta_db.close()
            return
        db = sqlite3.connect(args.db)
        if args.overwrite_geom:
            _log("--overwrite-geom: will overwrite existing geometry")
        process_cached(meta_db, db, args.dry_run)
        db.close()

    meta_db.close()


if __name__ == "__main__":
    main()
