"""Fetch USGS active stream gauge metadata via the OGC API.

Downloads site metadata from api.waterdata.usgs.gov for OR, WA, ID, NV, CA,
MT, filters to sites north of 40° latitude and west of -111° longitude
(skipped for MT, whose Pacific drainage extends east of -111° into Glacier
NP and the Bob Marshall — the HUC filter applied downstream is the real
boundary), and stores them in a 'usgs_site' table in the gauge metadata
cache.

Also queries time-series-metadata to record the most recent data date for
each site (flow, gage height, or temperature).
"""

import contextlib
import os
import sqlite3
import sys
import time
from pathlib import Path

import requests

STATES = ["Oregon", "Washington", "Idaho", "Nevada", "California", "Montana"]
OGC_BASE = "https://api.waterdata.usgs.gov/ogcapi/v0"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS usgs_site (
    site_no       TEXT PRIMARY KEY,
    station_nm    TEXT,
    latitude      REAL,
    longitude     REAL,
    state_cd      TEXT,
    county_cd     TEXT,
    huc_cd        TEXT,
    drain_area_sq_mi REAL,
    altitude_ft   REAL,
    alt_datum     TEXT,
    last_flow_date TEXT,
    last_gage_date TEXT,
    last_temp_date TEXT
)
"""

INSERT_SQL = """
INSERT OR REPLACE INTO usgs_site
    (site_no, station_nm, latitude, longitude, state_cd, county_cd,
     huc_cd, drain_area_sq_mi, altitude_ft, alt_datum)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

UPDATE_LAST_DATES = """
UPDATE usgs_site SET last_flow_date = ?, last_gage_date = ?, last_temp_date = ?
WHERE site_no = ?
"""


def fetch_page(url, api_key=None):
    """Fetch a single page, return parsed JSON. Retries on 429."""
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    for attempt in range(4):
        resp = requests.get(url, headers=headers, timeout=60)
        if resp.status_code == 429:
            wait = 2**attempt
            print(f"  Rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()

    raise RuntimeError(f"Gave up after rate-limit retries for {url}")


def fetch_state(state_name, api_key=None):
    """Fetch all stream gauge sites for one state via OGC API."""
    rows = []

    url = (
        f"{OGC_BASE}/collections/monitoring-locations/items"
        f"?f=json&limit=10000"
        f"&filter=state_name IN ('{state_name}') AND site_type_code='ST'"
        f"&filter-lang=cql-text"
    )

    while url:
        data = fetch_page(url, api_key)

        for feature in data.get("features", []):
            props = feature.get("properties", {})
            geom = feature.get("geometry")

            agency = props.get("agency_code", "")
            site_no = props.get("monitoring_location_number", "")

            if not site_no or agency != "USGS":
                continue

            if geom and geom.get("coordinates"):
                lon, lat = geom["coordinates"][0], geom["coordinates"][1]
            else:
                continue

            # Geographic bounding box for the OR/WA/ID/NV/CA cluster.
            # Skipped for MT because HUC4 1701 (Kootenai/Clark Fork/Flathead)
            # extends east of -111° into Glacier NP and the Bob Marshall;
            # the downstream HUC filter is the actual boundary for MT.
            if state_name != "Montana" and (lat < 40.0 or lon > -111.0):
                continue

            rows.append(
                {
                    "site_no": site_no,
                    "station_nm": props.get("monitoring_location_name", "").strip(),
                    "latitude": lat,
                    "longitude": lon,
                    "state_cd": props.get("state_code", ""),
                    "county_cd": props.get("county_code", ""),
                    "huc_cd": props.get("hydrologic_unit_code", ""),
                    "drain_area_sq_mi": props.get("drainage_area"),
                    "altitude_ft": props.get("altitude"),
                    "alt_datum": props.get("vertical_datum"),
                }
            )

        url = None
        for link in data.get("links", []):
            if link.get("rel") == "next":
                url = link.get("href")
                break

    return rows


def fetch_last_dates(site_nos, api_key=None):
    """Fetch most recent data dates from time-series-metadata.

    Returns dict of site_no -> {flow: date, gage: date, temp: date}.
    """
    # Map parameter codes to our keys
    param_keys = {"00060": "flow", "00065": "gage", "00010": "temp"}
    results = {}

    for param_code, key in param_keys.items():
        print(f"  Fetching last dates for {key} ({param_code})...", end=" ", flush=True)
        count = 0

        url = (
            f"{OGC_BASE}/collections/time-series-metadata/items"
            f"?f=json&limit=10000"
            f"&parameter_code={param_code}"
            f"&computation_identifier=Instantaneous"
            f"&filter-lang=cql-text"
        )

        while url:
            data = fetch_page(url, api_key)

            for feature in data.get("features", []):
                props = feature.get("properties", {})
                mon_id = props.get("monitoring_location_id", "")
                site_no = mon_id.replace("USGS-", "")

                if site_no not in site_nos:
                    continue

                end_date = props.get("end")
                if not end_date:
                    continue

                # Keep the most recent end date per site per param
                if site_no not in results:
                    results[site_no] = {}
                existing = results[site_no].get(key)
                if existing is None or end_date > existing:
                    results[site_no][key] = end_date
                    count += 1

            url = None
            for link in data.get("links", []):
                if link.get("rel") == "next":
                    url = link.get("href")
                    break

        print(f"{count} sites")

    return results


def main():
    db_path = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.environ.get(
            "GAUGE_METADATA_CACHE",
            str(Path(__file__).resolve().parent.parent / "Gauge-metadata-cache" / "gauges.db"),
        )
    )

    api_key = os.environ.get("USGS_API_KEY")

    conn = sqlite3.connect(db_path)
    conn.execute(CREATE_TABLE)
    # Add columns if they don't exist (upgrade from older schema)
    for col in ["last_flow_date", "last_gage_date", "last_temp_date"]:
        with contextlib.suppress(sqlite3.OperationalError):
            conn.execute(f"ALTER TABLE usgs_site ADD COLUMN {col} TEXT")

    total = 0
    all_site_nos = set()
    for state in STATES:
        print(f"Fetching {state}...", end=" ", flush=True)
        rows = fetch_state(state, api_key)
        print(f"{len(rows)} USGS stream sites")

        for r in rows:
            conn.execute(
                INSERT_SQL,
                (
                    r["site_no"],
                    r["station_nm"],
                    r["latitude"],
                    r["longitude"],
                    r["state_cd"],
                    r["county_cd"],
                    r["huc_cd"],
                    r["drain_area_sq_mi"],
                    r["altitude_ft"],
                    r["alt_datum"],
                ),
            )
            all_site_nos.add(r["site_no"])
        total += len(rows)

    conn.commit()
    print(f"\nTotal: {total} sites")

    # Fetch last data dates
    print("\nFetching time-series metadata for last data dates...")
    last_dates = fetch_last_dates(all_site_nos, api_key)

    updated = 0
    for site_no, dates in last_dates.items():
        conn.execute(
            UPDATE_LAST_DATES,
            (
                dates.get("flow"),
                dates.get("gage"),
                dates.get("temp"),
                site_no,
            ),
        )
        updated += 1

    conn.commit()
    print(f"Updated last data dates for {updated} sites")
    print(f"Stored in {db_path}")
    conn.close()


if __name__ == "__main__":
    main()
