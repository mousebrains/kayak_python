#!/usr/bin/env python3
"""Import AW reaches that reference USGS gauges into the kayak database.

Three phases:
  1. Create missing USGS gauges (gauge + source + gauge_source rows)
  2. Create reach records for unmatched AW reaches with USGS gauges
  3. Fetch AW geometry (geom) for new reaches via GraphQL API

Standalone script — uses only stdlib (sqlite3, urllib, json).

Usage:
    python3 scripts/import_aw_usgs_reaches.py --db kayak.db --dry-run
    python3 scripts/import_aw_usgs_reaches.py --db kayak.db
    python3 scripts/import_aw_usgs_reaches.py --db kayak.db --state OR
    python3 scripts/import_aw_usgs_reaches.py --db kayak.db --skip-geom
"""

import argparse
import io
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Force line-buffered stdout so progress is visible when piped
if not sys.stdout.line_buffering:
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding=sys.stdout.encoding,
        errors=sys.stdout.errors, line_buffering=True,
    )

DEFAULT_METADATA_DB = os.path.join(
    os.path.dirname(__file__), "..", "Gauge-metadata-cache", "gauges.db"
)

GRAPHQL_URL = "https://www.americanwhitewater.org/graphql"

# USGS fetch_url pattern — stateCd is lowercase two-letter abbreviation
USGS_URL_TEMPLATE = (
    "https://waterservices.usgs.gov/nwis/iv/"
    "?format=rdb&stateCd={state}&period=P1D&parameterCd=00060,00065,00010"
)

# Map AW state field (uppercase) to lowercase for USGS URLs
STATE_ABBREVS = {
    "AZ": "az", "CA": "ca", "CO": "co", "ID": "id", "KS": "ks",
    "MT": "mt", "NV": "nv", "NM": "nm", "OR": "or", "UT": "ut",
    "WA": "wa", "WY": "wy",
}

# USGS site service for batch metadata queries
USGS_SITE_URL = "https://waterservices.usgs.gov/nwis/site/"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

VERBOSE = False


def log(msg):
    print(msg, flush=True)


def vlog(msg):
    if VERBOSE:
        print(f"  {msg}", flush=True)


# ---------------------------------------------------------------------------
# USGS site metadata
# ---------------------------------------------------------------------------

def fetch_usgs_site_metadata(site_ids):
    """Batch-query USGS site service for metadata.

    Returns dict of site_id -> {name, lat, lon, elevation, drainage_area}.
    Queries in batches of 100 to stay within URL length limits.
    """
    results = {}
    batch_size = 100
    site_list = list(site_ids)

    for i in range(0, len(site_list), batch_size):
        batch = site_list[i:i + batch_size]
        params = urllib.parse.urlencode({
            "format": "rdb",
            "sites": ",".join(batch),
            "siteOutput": "expanded",
        })
        url = f"{USGS_SITE_URL}?{params}"
        vlog(f"Fetching USGS metadata for {len(batch)} sites...")

        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=60) as resp:
                text = resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            log(f"  WARNING: USGS site query failed: {e}")
            continue

        # Parse RDB format: skip comment lines (#), header line, width line
        lines = text.splitlines()
        header = None
        past_widths = False
        for line in lines:
            if line.startswith("#"):
                continue
            if header is None:
                header = line.split("\t")
                continue
            if not past_widths:
                past_widths = True  # skip the width/type line
                continue
            fields = line.split("\t")
            if len(fields) < len(header):
                continue
            row = dict(zip(header, fields))
            site_no = row.get("site_no", "").strip()
            if not site_no:
                continue
            results[site_no] = {
                "name": row.get("station_nm", "").strip(),
                "lat": _float(row.get("dec_lat_va")),
                "lon": _float(row.get("dec_long_va")),
                "elevation": _float(row.get("alt_va")),
                "drainage_area": _float(row.get("drain_area_va")),
            }

        if i + batch_size < len(site_list):
            time.sleep(1)  # be polite to USGS

    return results


def _float(val):
    """Convert a value to float, returning None for empty/invalid."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    val = str(val).strip()
    if not val:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# AW GraphQL for geometry
# ---------------------------------------------------------------------------

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


def fetch_aw_geom_batch(aw_ids):
    """Fetch geom for multiple AW reaches via aliased GraphQL query.

    Returns dict of aw_id -> geom_string (or None).
    """
    if not aw_ids:
        return {}
    fragments = []
    for aid in aw_ids:
        fragments.append(
            f'r{aid}: reach(id: {aid}) {{ geom }}'
        )
    query = "{\n" + "\n".join(fragments) + "\n}"
    result = graphql(query)
    out = {}
    for aid in aw_ids:
        info = result.get("data", {}).get(f"r{aid}")
        if info:
            out[aid] = info.get("geom")
        else:
            out[aid] = None
    return out


# ---------------------------------------------------------------------------
# Phase 1: Create missing USGS gauges
# ---------------------------------------------------------------------------

def get_usgs_fetch_url_map(db):
    """Return dict of lowercase state abbreviation -> fetch_url.id for USGS URLs."""
    rows = db.execute(
        "SELECT id, url FROM fetch_url WHERE url LIKE '%waterservices.usgs.gov%stateCd=%'"
    ).fetchall()
    result = {}
    for fid, url in rows:
        for part in url.split("&"):
            if part.startswith("stateCd="):
                st = part.split("=", 1)[1].lower()
                result[st] = fid
    return result


def create_missing_gauges(db, aw_reaches, dry_run):
    """Phase 1: Create gauge + source + gauge_source for USGS sites not in DB.

    Returns dict of usgs_site_id -> gauge_id (including both existing and new).
    """
    existing = {}
    for row in db.execute("SELECT id, usgs_id FROM gauge WHERE usgs_id IS NOT NULL"):
        existing[row[0]] = row[1]
    usgs_to_gauge = {v: k for k, v in existing.items()}

    existing_sources = {}
    for row in db.execute("SELECT id, name FROM source WHERE UPPER(agency) = 'USGS'"):
        existing_sources[row[1]] = row[0]

    needed_sites = set()
    for reach in aw_reaches:
        for g in reach.get("gauges", []):
            if g["source"] == "usgs":
                sid = g["source_id"]
                if sid not in usgs_to_gauge:
                    needed_sites.add(sid)

    if not needed_sites:
        log("Phase 1: No missing USGS gauges to create.")
        return usgs_to_gauge

    log(f"Phase 1: {len(needed_sites)} USGS sites need gauges")

    if not dry_run:
        metadata = fetch_usgs_site_metadata(needed_sites)
    else:
        metadata = {sid: {"name": f"USGS {sid}", "lat": None, "lon": None,
                          "elevation": None, "drainage_area": None}
                    for sid in needed_sites}

    fetch_url_map = get_usgs_fetch_url_map(db)

    site_to_state = {}
    for reach in aw_reaches:
        state = reach.get("state", "").upper()
        for g in reach.get("gauges", []):
            if g["source"] == "usgs" and g["source_id"] in needed_sites:
                site_to_state.setdefault(g["source_id"], state)

    missing_states = set()
    for sid, state in site_to_state.items():
        st_lower = STATE_ABBREVS.get(state, state.lower())
        if st_lower not in fetch_url_map:
            missing_states.add(st_lower)

    for st_lower in sorted(missing_states):
        url = USGS_URL_TEMPLATE.format(state=st_lower)
        label = f"[DRY-RUN] " if dry_run else ""
        log(f"  {label}Creating fetch_url for USGS state={st_lower}")
        if not dry_run:
            cur = db.execute(
                "INSERT INTO fetch_url (url, parser, is_active) VALUES (?, 'usgs', 1)",
                (url,),
            )
            fetch_url_map[st_lower] = cur.lastrowid

    created = 0
    for sid in sorted(needed_sites):
        meta = metadata.get(sid, {})
        gauge_name = meta.get("name") or f"USGS {sid}"
        lat = meta.get("lat")
        lon = meta.get("lon")
        elev = meta.get("elevation")
        drain = meta.get("drainage_area")

        state = site_to_state.get(sid, "")
        st_lower = STATE_ABBREVS.get(state, state.lower())
        fu_id = fetch_url_map.get(st_lower)

        label = "[DRY-RUN]" if dry_run else "CREATE"
        vlog(f"[{label}] gauge usgs_id={sid} name={gauge_name[:60]}")

        if not dry_run:
            cur = db.execute(
                """INSERT INTO gauge (name, usgs_id, latitude, longitude,
                   elevation, drainage_area)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (gauge_name, sid, lat, lon, elev, drain),
            )
            gauge_id = cur.lastrowid

            cur = db.execute(
                "INSERT INTO source (name, agency, fetch_url_id) VALUES (?, 'USGS', ?)",
                (sid, fu_id),
            )
            source_id = cur.lastrowid

            db.execute(
                "INSERT INTO gauge_source (gauge_id, source_id) VALUES (?, ?)",
                (gauge_id, source_id),
            )
            usgs_to_gauge[sid] = gauge_id
        else:
            usgs_to_gauge[sid] = -1  # sentinel

        created += 1

    if not dry_run:
        db.commit()

    log(f"Phase 1 complete: {created} gauges created, "
        f"{len(usgs_to_gauge)} total USGS gauges")
    return usgs_to_gauge


# ---------------------------------------------------------------------------
# Phase 2: Create reach records
# ---------------------------------------------------------------------------

def create_reaches(db, aw_reaches, usgs_to_gauge, dry_run):
    """Phase 2: Create reach + reach_state for unmatched AW reaches with USGS gauges.

    Returns list of (reach_id, aw_id) for newly created reaches.
    """
    state_map = {}
    for row in db.execute("SELECT id, abbreviation FROM state WHERE abbreviation IS NOT NULL"):
        state_map[row[1].upper()] = row[0]

    source_to_gauge = {}
    for row in db.execute(
        """SELECT s.name, gs.gauge_id FROM source s
           JOIN gauge_source gs ON gs.source_id = s.id
           WHERE UPPER(s.agency) = 'USGS'"""
    ):
        source_to_gauge[row[0]] = row[1]

    created = 0
    new_reaches = []

    for reach in aw_reaches:
        aw_id = reach["id"]
        state = reach.get("state", "").upper()

        gauge_id = None
        for g in reach.get("gauges", []):
            if g["source"] == "usgs":
                sid = g["source_id"]
                if sid in source_to_gauge:
                    gauge_id = source_to_gauge[sid]
                    break
                if sid in usgs_to_gauge:
                    gauge_id = usgs_to_gauge[sid]
                    break

        if gauge_id is None:
            vlog(f"SKIP aw_id={aw_id}: no gauge found")
            continue

        river = reach.get("river") or ""
        section = reach.get("section") or ""
        display_name = river
        description = section if section else None
        name = f"aw_{aw_id}"

        plat = _float(reach.get("plat"))
        plon = _float(reach.get("plon"))
        tlat = _float(reach.get("tlat"))
        tlon = _float(reach.get("tlon"))

        lat = None
        lon = None
        if plat is not None and tlat is not None:
            lat = (plat + tlat) / 2.0
        elif plat is not None:
            lat = plat
        elif tlat is not None:
            lat = tlat
        if plon is not None and tlon is not None:
            lon = (plon + tlon) / 2.0
        elif plon is not None:
            lon = plon
        elif tlon is not None:
            lon = tlon

        length = _float(reach.get("length"))
        gradient = _float(reach.get("avggradient"))
        max_gradient = _float(reach.get("maxgradient"))
        difficulties = reach.get("class")

        label = "[DRY-RUN]" if dry_run else "CREATE"
        vlog(f"[{label}] reach {name}: {display_name[:60]} gauge={gauge_id}")

        if not dry_run:
            cur = db.execute(
                """INSERT INTO reach
                   (name, display_name, sort_name, river, gauge_id,
                    description, difficulties, length, gradient, max_gradient,
                    latitude_start, longitude_start,
                    latitude_end, longitude_end,
                    latitude, longitude,
                    aw_id, no_show)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (name, display_name, display_name, river, gauge_id,
                 description, difficulties, length, gradient, max_gradient,
                 plat, plon, tlat, tlon, lat, lon, aw_id),
            )
            reach_id = cur.lastrowid

            state_id = state_map.get(state)
            if state_id:
                db.execute(
                    "INSERT OR IGNORE INTO reach_state (reach_id, state_id) VALUES (?, ?)",
                    (reach_id, state_id),
                )

            rmin = None
            rmax = None
            for g in reach.get("gauges", []):
                if g.get("rmin") is not None:
                    rmin = _float(g["rmin"])
                if g.get("rmax") is not None:
                    rmax = _float(g["rmax"])
            if rmin is not None or rmax is not None:
                if rmin is not None:
                    db.execute(
                        "INSERT INTO reach_level "
                        "(reach_id, level, low, low_data_type, high, high_data_type) "
                        "VALUES (?, 'low', NULL, 'flow', ?, 'flow')",
                        (reach_id, rmin),
                    )
                if rmin is not None and rmax is not None:
                    db.execute(
                        "INSERT INTO reach_level "
                        "(reach_id, level, low, low_data_type, high, high_data_type) "
                        "VALUES (?, 'okay', ?, 'flow', ?, 'flow')",
                        (reach_id, rmin, rmax),
                    )
                if rmax is not None:
                    db.execute(
                        "INSERT INTO reach_level "
                        "(reach_id, level, low, low_data_type, high, high_data_type) "
                        "VALUES (?, 'high', ?, 'flow', NULL, 'flow')",
                        (reach_id, rmax),
                    )

            new_reaches.append((reach_id, aw_id))

        created += 1

    if not dry_run:
        db.commit()

    log(f"Phase 2 complete: {created} reaches created")
    return new_reaches


# ---------------------------------------------------------------------------
# Phase 3: Fetch AW geometry
# ---------------------------------------------------------------------------

def fetch_geometry(db, new_reaches, dry_run):
    """Phase 3: Fetch geom from AW GraphQL API for new reaches."""
    if not new_reaches:
        log("Phase 3: No reaches to fetch geometry for.")
        return

    log(f"Phase 3: Fetching geometry for {len(new_reaches)} reaches...")

    batch_size = 20
    total_fetched = 0
    total_with_geom = 0

    for i in range(0, len(new_reaches), batch_size):
        batch = new_reaches[i:i + batch_size]
        aw_ids = [aw_id for _, aw_id in batch]

        try:
            geom_map = fetch_aw_geom_batch(aw_ids)
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as e:
            log(f"  WARNING: geom batch failed: {e}")
            time.sleep(2)
            continue

        for reach_id, aw_id in batch:
            geom = geom_map.get(aw_id)
            if geom and not dry_run:
                db.execute(
                    "UPDATE reach SET geom = ? WHERE id = ?",
                    (geom, reach_id),
                )
                total_with_geom += 1
            elif geom:
                total_with_geom += 1

        total_fetched += len(batch)
        done_pct = total_fetched * 100 // len(new_reaches)
        vlog(f"Geometry: {total_fetched}/{len(new_reaches)} ({done_pct}%)")

        if i + batch_size < len(new_reaches):
            time.sleep(2)  # rate limit AW API

    if not dry_run:
        db.commit()

    log(f"Phase 3 complete: {total_with_geom}/{len(new_reaches)} "
        f"reaches have geometry")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_aw_reaches(meta_db, state_filter=None):
    """Load AW reaches from metadata DB, filter to those with USGS gauges."""
    query = "SELECT id, river, section, class, state, put_in_lat, put_in_lon, " \
            "take_out_lat, take_out_lon, length, avg_gradient, max_gradient, gauges " \
            "FROM aw_reach WHERE gauges IS NOT NULL"
    params = []
    if state_filter:
        query += " AND UPPER(state) = ?"
        params.append(state_filter)

    rows = meta_db.execute(query, params).fetchall()

    reaches = []
    for row in rows:
        gauges = json.loads(row[12]) if row[12] else []
        usgs_gauges = [g for g in gauges if g.get("source") == "usgs"]
        if not usgs_gauges:
            continue
        reaches.append({
            "id": row[0],
            "river": row[1],
            "section": row[2],
            "class": row[3],
            "state": row[4],
            "plat": row[5],
            "plon": row[6],
            "tlat": row[7],
            "tlon": row[8],
            "length": row[9],
            "avggradient": row[10],
            "maxgradient": row[11],
            "gauges": gauges,
        })

    return reaches


def filter_unmatched(db, aw_reaches):
    """Remove AW reaches that already have a matching reach in the DB (by aw_id)."""
    existing_aw_ids = set()
    for row in db.execute("SELECT aw_id FROM reach WHERE aw_id IS NOT NULL"):
        existing_aw_ids.add(row[0])

    unmatched = [r for r in aw_reaches if r["id"] not in existing_aw_ids]
    log(f"AW reaches with USGS gauges: {len(aw_reaches)}, "
        f"already matched: {len(aw_reaches) - len(unmatched)}, "
        f"to import: {len(unmatched)}")
    return unmatched


def main():
    parser = argparse.ArgumentParser(
        description="Import AW reaches with USGS gauges into kayak database"
    )
    parser.add_argument("--db", default=os.path.join(os.path.dirname(__file__), "..", "..", "DB", "kayak.db"),
                        help="SQLite database path")
    parser.add_argument("--metadata-db", default=os.path.abspath(DEFAULT_METADATA_DB),
                        help="Gauge metadata cache DB path")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without modifying DB")
    parser.add_argument("--state",
                        help="Process only this state (e.g., OR)")
    parser.add_argument("--skip-geom", action="store_true",
                        help="Skip Phase 3 (geometry fetch)")
    parser.add_argument("--verbose", action="store_true",
                        help="Show detailed per-row output")
    args = parser.parse_args()

    global VERBOSE
    VERBOSE = args.verbose

    state_filter = args.state.upper() if args.state else None

    # Load AW reaches from metadata DB
    meta_db = sqlite3.connect(args.metadata_db)
    aw_reaches = load_aw_reaches(meta_db, state_filter)
    meta_db.close()

    if not aw_reaches:
        log("No AW reaches with USGS gauges found in metadata DB.")
        log("Run scripts/match_aw_reaches.py first to populate the AW cache.")
        return

    db = sqlite3.connect(args.db)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")

    aw_reaches = filter_unmatched(db, aw_reaches)
    if not aw_reaches:
        log("All AW reaches with USGS gauges are already imported.")
        db.close()
        return

    if args.dry_run:
        log("=== DRY RUN — no changes will be made ===\n")

    # Phase 1: Create missing gauges
    usgs_to_gauge = create_missing_gauges(db, aw_reaches, args.dry_run)

    # Phase 2: Create reaches
    new_reaches = create_reaches(db, aw_reaches, usgs_to_gauge, args.dry_run)

    # Phase 3: Fetch geometry
    if not args.skip_geom and not args.dry_run:
        fetch_geometry(db, new_reaches, args.dry_run)
    elif args.skip_geom:
        log("Phase 3: Skipped (--skip-geom)")
    elif args.dry_run:
        log(f"Phase 3: Skipped (dry run; would fetch geom for "
            f"{len(new_reaches)} reaches)")

    db.close()
    log("\nDone.")


if __name__ == "__main__":
    main()
