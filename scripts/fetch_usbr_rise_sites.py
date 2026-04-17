"""Fetch USBR RISE location metadata and store in the gauge metadata cache.

Downloads all locations from https://data.usbr.gov/rise/api/location (paginated),
fetches catalog-records and catalog-items to compute the most recent data date
per location, and stores them in a ``usbr_rise_site`` table.

Notes on "active":
    RISE exposes ``locationStatusId`` but every published location has status=1,
    so it's not useful for distinguishing live vs retired gauges. Real activity
    is inferred from ``temporalEndDate`` on the catalog-items linked to each
    location via its catalog-records. We take max(temporalEndDate) per location
    and call it ``last_data_date``; ``is_active`` is 1 when that date is within
    ACTIVE_WINDOW_DAYS, else 0.

    Empirically (2026-04-17 harvest): RISE carries operational time-series for
    USBR Central Valley / Great Plains / Colorado River / Missouri Basin
    reservoirs and dams. Pacific Northwest stream/canal gauges (OR/WA/ID) have
    location metadata in RISE but no linked time-series — live data for those
    flows through the pn-hydromet ``instant.pl`` service (see parsers/usbr.py).
    So OR/WA/ID rows will typically show ``is_active=0`` here even when the
    gauge is live upstream. Cross-reference ``source_code`` with pn-hydromet
    station lists for true PNW activity status.

Run:
    python scripts/fetch_usbr_rise_sites.py [path/to/gauges.db]
"""

import re
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests

RISE_BASE = "https://data.usbr.gov/rise/api"
PAGE_SIZE = 100
ACTIVE_WINDOW_DAYS = 30

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS usbr_rise_site (
    rise_id          INTEGER PRIMARY KEY,
    name             TEXT,
    source_code      TEXT,
    type_name        TEXT,
    status_id        INTEGER,
    latitude         REAL,
    longitude        REAL,
    geometry_type    TEXT,
    elevation_ft     REAL,
    states           TEXT,
    unified_region   TEXT,
    timezone         TEXT,
    horizontal_datum TEXT,
    create_date      TEXT,
    update_date      TEXT,
    last_data_date   TEXT,
    is_active        INTEGER
)
"""

# Trailing parenthesized token in locationName, e.g. "… (ROMO)" → "ROMO".
SOURCE_CODE_RE = re.compile(r"\(([A-Z0-9]{2,6})\)\s*$")


def fetch_paginated(session: requests.Session, endpoint: str) -> list[dict]:
    """Fetch all pages of a RISE list endpoint."""
    page = 1
    out: list[dict] = []
    while True:
        url = f"{RISE_BASE}{endpoint}?itemsPerPage={PAGE_SIZE}&page={page}"
        resp = session.get(url, timeout=60)
        resp.raise_for_status()
        body = resp.json()
        batch = body.get("data") or []
        if not batch:
            break
        out.extend(batch)
        total = body.get("meta", {}).get("totalItems", 0)
        if len(out) >= total:
            break
        page += 1
        print(f"    {endpoint}: {len(out)}/{total}", end="\r", flush=True)
    print(f"    {endpoint}: {len(out)} fetched" + " " * 20)
    return out


def extract_lat_lon(coords: dict | None) -> tuple[float | None, float | None]:
    """Extract (lat, lon) from a GeoJSON locationCoordinates dict.

    Point → the point. Polygon/LineString → centroid of vertices.
    Returns (None, None) if coordinates are missing or malformed.
    """
    if not coords or not coords.get("coordinates"):
        return None, None
    pts: list[tuple[float, float]] = []

    def walk(x: object) -> None:
        if (
            isinstance(x, list)
            and len(x) >= 2
            and isinstance(x[0], int | float)
            and isinstance(x[1], int | float)
        ):
            pts.append((float(x[1]), float(x[0])))  # (lat, lon) from [lon, lat]
        elif isinstance(x, list):
            for sub in x:
                walk(sub)

    walk(coords["coordinates"])
    if not pts:
        return None, None
    lat = sum(p[0] for p in pts) / len(pts)
    lon = sum(p[1] for p in pts) / len(pts)
    return lat, lon


def main() -> None:
    db_path = (
        sys.argv[1]
        if len(sys.argv) > 1
        else str(Path(__file__).resolve().parent.parent / "Gauge-metadata-cache" / "gauges.db")
    )

    session = requests.Session()
    session.headers.update({"Accept": "application/vnd.api+json"})

    print("Fetching RISE locations...")
    locations = fetch_paginated(session, "/location")

    print("Fetching catalog-records...")
    records = fetch_paginated(session, "/catalog-record")

    print("Fetching catalog-items...")
    items = fetch_paginated(session, "/catalog-item")

    # Item → temporalEndDate
    item_end: dict[int, str] = {}
    for it in items:
        attr = it["attributes"]
        end = attr.get("temporalEndDate")
        if end:
            item_end[attr["_id"]] = end

    # Record → (location_id, max temporalEndDate across items)
    record_location: dict[int, int | None] = {}
    record_end: dict[int, str | None] = {}
    for rec in records:
        attr = rec["attributes"]
        rid = attr["_id"]
        rel = rec.get("relationships", {})
        loc = rel.get("location", {}).get("data")
        record_location[rid] = (
            int(loc["id"].split("/")[-1]) if loc and isinstance(loc, dict) else None
        )
        max_end: str | None = None
        for item_rel in rel.get("catalogItems", {}).get("data", []) or []:
            iid = int(item_rel["id"].split("/")[-1])
            end = item_end.get(iid)
            if end and (max_end is None or end > max_end):
                max_end = end
        record_end[rid] = max_end

    # Location → max temporalEndDate (across all its records)
    location_end: dict[int, str] = {}
    for rid, lid in record_location.items():
        if lid is None:
            continue
        end = record_end.get(rid)
        if end and (lid not in location_end or end > location_end[lid]):
            location_end[lid] = end

    cutoff = (datetime.now(UTC) - timedelta(days=ACTIVE_WINDOW_DAYS)).isoformat()

    rows: list[dict] = []
    for loc in locations:
        attr = loc["attributes"]
        rid = attr["_id"]
        coords = attr.get("locationCoordinates")
        lat, lon = extract_lat_lon(coords)
        name = attr.get("locationName") or ""
        src_match = SOURCE_CODE_RE.search(name)
        elev_raw = attr.get("elevation")
        try:
            elev = float(elev_raw) if elev_raw is not None else None
        except (TypeError, ValueError):
            elev = None
        states = ",".join(
            s["id"].split("/")[-1]
            for s in loc.get("relationships", {}).get("states", {}).get("data", []) or []
        )
        last_data = location_end.get(rid)
        rows.append(
            {
                "rise_id": rid,
                "name": name,
                "source_code": src_match.group(1) if src_match else None,
                "type_name": attr.get("locationTypeName"),
                "status_id": attr.get("locationStatusId"),
                "latitude": lat,
                "longitude": lon,
                "geometry_type": (coords or {}).get("type"),
                "elevation_ft": elev,
                "states": states,
                "unified_region": ",".join(attr.get("locationUnifiedRegionNames") or []),
                "timezone": attr.get("timezone"),
                "horizontal_datum": (attr.get("horizontalDatum") or {}).get("_id"),
                "create_date": attr.get("createDate"),
                "update_date": attr.get("updateDate"),
                "last_data_date": last_data,
                "is_active": 1 if (last_data and last_data >= cutoff) else 0,
            }
        )

    conn = sqlite3.connect(db_path)
    conn.execute(CREATE_TABLE)
    conn.executemany(
        """INSERT OR REPLACE INTO usbr_rise_site (
            rise_id, name, source_code, type_name, status_id,
            latitude, longitude, geometry_type, elevation_ft,
            states, unified_region, timezone, horizontal_datum,
            create_date, update_date, last_data_date, is_active
        ) VALUES (
            :rise_id, :name, :source_code, :type_name, :status_id,
            :latitude, :longitude, :geometry_type, :elevation_ft,
            :states, :unified_region, :timezone, :horizontal_datum,
            :create_date, :update_date, :last_data_date, :is_active
        )""",
        rows,
    )
    conn.commit()

    active_total = sum(r["is_active"] for r in rows)
    print(
        f"\nStored {len(rows)} sites in {db_path} "
        f"({active_total} active within {ACTIVE_WINDOW_DAYS} days)"
    )

    cur = conn.execute(
        """SELECT type_name,
                  COUNT(*) AS total,
                  SUM(is_active) AS active,
                  SUM(CASE WHEN latitude IS NOT NULL THEN 1 ELSE 0 END) AS with_latlon
             FROM usbr_rise_site
            GROUP BY type_name
            ORDER BY total DESC"""
    )
    print(f"\n{'Type':<28} {'total':>6} {'active':>7} {'w/ latlon':>10}")
    for type_name, total, active, with_latlon in cur:
        print(f"  {type_name or 'None':<26} {total:>6} {active or 0:>7} {with_latlon:>10}")
    conn.close()


if __name__ == "__main__":
    main()
