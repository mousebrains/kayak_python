"""Fetch NWS/NWPS gauge metadata and store in the gauge metadata cache.

Downloads gauge metadata from the NWPS API, filters to sites north of 40°
latitude and west of -111° longitude, and stores them in an 'nwps_site' table.
"""

import sqlite3
import sys

import requests

NWPS_URL = "https://api.water.noaa.gov/nwps/v1/gauges"
STATES = ["OR", "WA", "ID", "NV", "CA"]

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS nwps_site (
    lid           TEXT PRIMARY KEY,
    name          TEXT,
    latitude      REAL,
    longitude     REAL,
    state         TEXT,
    rfc           TEXT,
    wfo           TEXT,
    pedts_observed TEXT,
    pedts_forecast TEXT
)
"""

INSERT_SQL = """
INSERT OR REPLACE INTO nwps_site
    (lid, name, latitude, longitude, state, rfc, wfo, pedts_observed, pedts_forecast)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else "/home/pat/kayak/Gauge-metadata-cache/gauges.db"

    filtered = []
    for state in STATES:
        print(f"Fetching {state}...", end=" ", flush=True)
        resp = requests.get(NWPS_URL, params={"state": state}, timeout=120)
        resp.raise_for_status()
        gauges = resp.json()["gauges"]
        kept = [
            g
            for g in gauges
            if g.get("latitude")
            and g.get("longitude")
            and g["latitude"] >= 40.0
            and g["longitude"] <= -111.0
        ]
        print(f"{len(kept)} sites (of {len(gauges)})")
        filtered.extend(kept)
    print(f"  Total after filtering: {len(filtered)}")

    conn = sqlite3.connect(db_path)
    conn.execute(CREATE_TABLE)

    for g in filtered:
        pedts = g.get("pedts", {})
        conn.execute(
            INSERT_SQL,
            (
                g["lid"],
                g.get("name"),
                g["latitude"],
                g["longitude"],
                g.get("state", {}).get("abbreviation"),
                g.get("rfc", {}).get("abbreviation"),
                g.get("wfo", {}).get("abbreviation"),
                pedts.get("observed"),
                pedts.get("forecast"),
            ),
        )

    conn.commit()
    print(f"\n{len(filtered)} NWPS sites stored in {db_path}")

    # Summary by state
    cur = conn.execute(
        "SELECT state, COUNT(*) FROM nwps_site GROUP BY state ORDER BY COUNT(*) DESC"
    )
    for state, count in cur:
        print(f"  {state}: {count}")

    conn.close()


if __name__ == "__main__":
    main()
