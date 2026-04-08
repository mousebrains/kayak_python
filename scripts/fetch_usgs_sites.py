"""Fetch USGS active stream gauge metadata and store in a local SQLite table.

Downloads site metadata from the USGS NWIS site service for OR, WA, ID, NV, CA,
filters to sites north of 40° latitude and west of -111° longitude, and stores
them in a 'usgs_site' table in kayak.db.
"""

import csv
import io
import sqlite3
import sys

import requests

STATES = ["or", "wa", "id", "nv", "ca"]
BASE_URL = "https://waterservices.usgs.gov/nwis/site/"

# Columns we care about from the expanded site output
KEEP_COLS = [
    "site_no", "station_nm", "dec_lat_va", "dec_long_va",
    "state_cd", "county_cd", "huc_cd", "drain_area_va",
    "alt_va", "alt_datum_cd",
]

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
    alt_datum     TEXT
)
"""

INSERT_SQL = """
INSERT OR REPLACE INTO usgs_site
    (site_no, station_nm, latitude, longitude, state_cd, county_cd,
     huc_cd, drain_area_sq_mi, altitude_ft, alt_datum)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def fetch_state(state_cd: str) -> list[dict]:
    """Fetch site metadata for one state, return list of row dicts."""
    params = {
        "format": "rdb",
        "stateCd": state_cd,
        "siteType": "ST",
        "siteStatus": "active",
        "hasDataTypeCd": "iv",
        "siteOutput": "expanded",
    }
    resp = requests.get(BASE_URL, params=params, timeout=60)
    resp.raise_for_status()

    # Strip comment lines and the format-description line
    lines = [l for l in resp.text.splitlines() if not l.startswith("#")]
    if len(lines) < 2:
        return []

    # First line is headers, second is format widths (skip it), rest is data
    header = lines[0]
    data_lines = lines[2:]

    reader = csv.DictReader(io.StringIO("\n".join([header] + data_lines)), delimiter="\t")
    rows = []
    for row in reader:
        try:
            lat = float(row.get("dec_lat_va") or "")
            lon = float(row.get("dec_long_va") or "")
        except ValueError:
            continue

        # Filter: north of 40°, west of -111°
        if lat < 40.0 or lon > -111.0:
            continue

        def to_float(val):
            try:
                return float(val.strip()) if val and val.strip() else None
            except ValueError:
                return None

        rows.append({
            "site_no": row["site_no"].strip(),
            "station_nm": row["station_nm"].strip(),
            "latitude": lat,
            "longitude": lon,
            "state_cd": row.get("state_cd", "").strip(),
            "county_cd": row.get("county_cd", "").strip(),
            "huc_cd": row.get("huc_cd", "").strip(),
            "drain_area_sq_mi": to_float(row.get("drain_area_va")),
            "altitude_ft": to_float(row.get("alt_va")),
            "alt_datum": row.get("alt_datum_cd", "").strip() or None,
        })

    return rows


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else "/home/pat/kayak/Gauge-metadata-cache/gauges.db"

    conn = sqlite3.connect(db_path)
    conn.execute(CREATE_TABLE)

    total = 0
    for state in STATES:
        print(f"Fetching {state.upper()}...", end=" ", flush=True)
        rows = fetch_state(state)
        print(f"{len(rows)} sites")

        for r in rows:
            conn.execute(INSERT_SQL, (
                r["site_no"], r["station_nm"], r["latitude"], r["longitude"],
                r["state_cd"], r["county_cd"], r["huc_cd"],
                r["drain_area_sq_mi"], r["altitude_ft"], r["alt_datum"],
            ))
        total += len(rows)

    conn.commit()
    print(f"\nTotal: {total} sites stored in {db_path}")
    conn.close()


if __name__ == "__main__":
    main()
