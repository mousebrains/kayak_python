"""Fetch NWS/NWPS gauge metadata and store in the gauge metadata cache.

Downloads gauge metadata from the NWPS API and stores all US gauges in an
'nwps_site' table. Storage is cheap (~12k rows) and a full cache lets us
cover CNRFC/MBRFC/CBRFC etc. alongside the main PNW set.
"""

import os
import sqlite3
import sys
import time
from pathlib import Path

import requests

NWPS_URL = "https://api.water.noaa.gov/nwps/v1/gauges"

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
    db_path = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.environ.get(
            "GAUGE_METADATA_CACHE",
            str(Path(__file__).resolve().parent.parent / "Gauge-metadata-cache" / "gauges.db"),
        )
    )

    # NWPS returns the same full gauge list regardless of state= parameter, and the
    # endpoint throttles under repeated calls (504). Fetch once with retries.
    print("Fetching NWPS gauges (one request, retry on 504)...", flush=True)
    for attempt in range(4):
        try:
            resp = requests.get(NWPS_URL, timeout=180)
            resp.raise_for_status()
            break
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 504 and attempt < 3:
                wait = 2 ** (attempt + 2)
                print(f"  504; retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise
    gauges = resp.json()["gauges"]
    filtered = [g for g in gauges if g.get("latitude") and g.get("longitude")]
    print(f"  {len(filtered)} sites (of {len(gauges)} total)")

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
