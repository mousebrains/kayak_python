#!/usr/bin/env python3
"""Harvest WA Ecology flow-monitoring station metadata into gauges.db.

Source: https://gis.ecology.wa.gov/serverext/rest/services/EAP/FlowMonitoringStations/MapServer/0
Backs the public app https://gis.ecology.wa.gov/portal/apps/instant/basic/index.html?appid=fb8ab17802754f689a0025414c4b8d66
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

LAYER = "https://gis.ecology.wa.gov/serverext/rest/services/EAP/FlowMonitoringStations/MapServer/0"
DB = Path("/home/pat/kayak/Gauge-metadata-cache/gauges.db")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
LATEST_RE = re.compile(r"Latest values as of\s+(\d+/\d+/\d+\s+\d+:\d+:\d+\s+[AP]M)")


def fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def parse_latest_update(msg: str | None) -> str | None:
    """Extract 'Latest values as of M/D/YYYY H:MM:SS AM/PM' from StationMessage.
    Detail pages don't carry this; it only lives in the FeatureServer's cached msg.
    Returns ISO 8601 seconds or None.
    """
    if not msg:
        return None
    m = LATEST_RE.search(msg)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%m/%d/%Y %I:%M:%S %p").isoformat(timespec="seconds")
    except ValueError:
        return None


def main() -> int:
    q = (
        f"{LAYER}/query?where=1%3D1&outFields=*&returnGeometry=true"
        "&outSR=4326&f=json&resultRecordCount=2000"
    )
    data = fetch(q)
    feats = data.get("features", [])
    if not feats:
        print("No features returned", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wa_ecology_site (
            station_id         TEXT PRIMARY KEY,
            station_name       TEXT,
            station_type       TEXT,
            type_description   TEXT,
            period_of_record   TEXT,
            status             TEXT,
            coop_station       INTEGER,
            station_message    TEXT,
            flow_monitoring    INTEGER,
            precip_monitoring  INTEGER,
            wq_monitoring      INTEGER,
            station_link       TEXT,
            latitude           REAL,
            longitude          REAL,
            latest_update      TEXT
        )
    """)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(wa_ecology_site)")}
    if "latest_update" not in cols:
        conn.execute("ALTER TABLE wa_ecology_site ADD COLUMN latest_update TEXT")

    inserted = 0
    for f in feats:
        a = f.get("attributes") or {}
        g = f.get("geometry") or {}
        sid = a.get("StationID")
        if not sid:
            continue
        msg = a.get("StationMessage")
        conn.execute(
            """INSERT OR REPLACE INTO wa_ecology_site
               (station_id, station_name, station_type, type_description,
                period_of_record, status, coop_station, station_message,
                flow_monitoring, precip_monitoring, wq_monitoring,
                station_link, latitude, longitude, latest_update)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sid,
                a.get("StationName"),
                a.get("StationType"),
                a.get("TypeDescription"),
                a.get("PeriodOfRecord"),
                a.get("Status"),
                a.get("CoopStation"),
                msg,
                a.get("FlowMonitoring"),
                a.get("PrecipMonitoring"),
                a.get("WQMonitoring"),
                a.get("StationLink"),
                g.get("y"),
                g.get("x"),
                parse_latest_update(msg),
            ),
        )
        inserted += 1
    conn.commit()
    print(f"Harvested {inserted} WA Ecology stations")

    c = conn.execute("SELECT status, COUNT(*) FROM wa_ecology_site GROUP BY status ORDER BY 2 DESC")
    print("\nBy status:")
    for row in c:
        print(f"  {row[0] or '(null)':<20}  {row[1]}")
    c = conn.execute(
        "SELECT station_type, COUNT(*) FROM wa_ecology_site GROUP BY station_type ORDER BY 2 DESC"
    )
    print("\nBy type:")
    for row in c:
        print(f"  {row[0] or '(null)':<40}  {row[1]}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
