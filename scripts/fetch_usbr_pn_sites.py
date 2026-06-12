"""Harvest USBR Pacific Northwest pn-hydromet station metadata.

pn-hydromet is USBR's older Pacific Northwest regional service (usbr.gov/pn).
It lacks a clean JSON discovery endpoint, but metadata can be stitched from:

  1. ``https://www.usbr.gov/pn/hydromet/station.js`` — JS fragment containing
     ``<option value="CODE">CODE - Station Name</option>`` for every station
     in the selection dropdown. ~442 stations, kept current.
  2. ``https://www.usbr.gov/pn-bin/inventory.pl?site=<code>&interval=instant``
     — per-station parameter inventory, with "available records" date ranges
     whose end year reveals whether the gauge is still reporting.
  3. ``https://www.usbr.gov/pn/hydromet/decod_params.html`` — DMS-formatted
     lat/lon for most stations, frozen at 2006. Used as fallback for stations
     not in RISE.
  4. ``usbr_rise_site`` table (populated by fetch_usbr_rise_sites.py) — current
     decimal lat/lon for the ~3 pn-hydromet stations that also appear in RISE.

Active gauge definition (is_active=1): at least one parameter in the inventory
has an end year >= current year - 1 (e.g., during 2026 we accept 2025 or 2026).

Run:
    python scripts/fetch_usbr_pn_sites.py [path/to/gauges.db]
"""

from __future__ import annotations

import concurrent.futures
import os
import re
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import requests

STATION_JS_URL = "https://www.usbr.gov/pn/hydromet/station.js"
DECOD_URL = "https://www.usbr.gov/pn/hydromet/decod_params.html"
INVENTORY_URL = "https://www.usbr.gov/pn-bin/inventory.pl"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS pn_hydromet_site (
    code            TEXT PRIMARY KEY,
    name            TEXT,
    latitude        REAL,
    longitude       REAL,
    elevation_ft    REAL,
    latlon_source   TEXT,
    parameters      TEXT,
    last_year       INTEGER,
    is_active       INTEGER,
    fetched_at      TEXT
)
"""

OPTION_RE = re.compile(r'<option value="([^"]+)">\s*([^<]+?)\s*</option>', re.IGNORECASE)
DECOD_RE = re.compile(
    r"^\s{2,}([A-Z0-9]{2,6})\s+(.+?)\s*\n"
    r"(?:\s+.*?\n)?"
    r"\s+LAT\s*=\s*(\S+)\s+LONG\s*=\s*(\S+)(?:\s+ELEV\s*=\s*(\S+))?",
    re.MULTILINE,
)
INVENTORY_ROW_RE = re.compile(
    r"<tr>\s*<td>\s*([^<]+?)\s*</td>\s*<td>\s*([^<]*?)\s*</td>\s*<td>\s*([^<]*?)\s*</td>\s*</tr>",
    re.IGNORECASE,
)
YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")


def fetch_station_list(session: requests.Session) -> dict[str, str]:
    """Return {code: name} for every pn-hydromet station."""
    resp = session.get(STATION_JS_URL, timeout=60)
    resp.raise_for_status()
    out: dict[str, str] = {}
    for code, label in OPTION_RE.findall(resp.text):
        code = code.strip().upper()
        name = label.strip()
        # Labels are formatted "CODE - Station Name"; strip the redundant prefix.
        prefix = f"{code} - "
        if name.startswith(prefix):
            name = name[len(prefix) :]
        out[code] = name
    return out


def dms_to_decimal(dms: str) -> float | None:
    """Convert DMS string like '43-57-37' to decimal degrees."""
    parts = dms.strip().split("-")
    if len(parts) < 2:
        return None
    try:
        deg = int(parts[0])
        minutes = int(parts[1]) if len(parts) > 1 else 0
        seconds = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return None
    return deg + minutes / 60.0 + seconds / 3600.0


def fetch_decod_latlon(session: requests.Session) -> dict[str, tuple[float, float, float | None]]:
    """Parse decod_params.html → {code: (lat, lon, elev_ft)}.

    Longitude is stored as a negative value (Pacific Northwest is West of meridian).
    """
    resp = session.get(DECOD_URL, timeout=60)
    resp.raise_for_status()
    pre = re.search(r"<pre[^>]*>(.*?)</pre>", resp.text, re.DOTALL | re.IGNORECASE)
    if not pre:
        return {}
    text = re.sub(r"<[^>]+>", "", pre.group(1))
    out: dict[str, tuple[float, float, float | None]] = {}
    for match in DECOD_RE.finditer(text):
        code = match.group(1).strip().upper()
        lat_dms = match.group(3)
        lon_dms = match.group(4)
        elev_raw = match.group(5)
        lat = dms_to_decimal(lat_dms)
        lon_abs = dms_to_decimal(lon_dms)
        if lat is None or lon_abs is None:
            continue
        try:
            elev = float(elev_raw) if elev_raw else None
        except ValueError:
            elev = None
        out[code] = (lat, -lon_abs, elev)
    return out


def fetch_rise_latlon(db_path: str) -> dict[str, tuple[float, float, float | None]]:
    """Read {source_code: (lat, lon, elev_ft)} from usbr_rise_site, if populated."""
    out: dict[str, tuple[float, float, float | None]] = {}
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            """SELECT source_code, latitude, longitude, elevation_ft
                 FROM usbr_rise_site
                WHERE source_code IS NOT NULL
                  AND latitude IS NOT NULL
                  AND longitude IS NOT NULL"""
        )
        for code, lat, lon, elev in cur:
            out[code.upper()] = (lat, lon, elev)
        conn.close()
    except sqlite3.OperationalError:
        # Table doesn't exist yet — RISE harvest hasn't run.
        pass
    return out


def fetch_inventory(session: requests.Session, code: str) -> tuple[list[str], int | None]:
    """Return (parameter codes, max_end_year) for one pn-hydromet station.

    Parses the inventory.pl HTML for rows like
        <tr><td>q</td><td>Discharge, cfs</td><td>1982-2026</td></tr>
    """
    params = {"site": code.lower(), "interval": "instant"}
    try:
        resp = session.get(INVENTORY_URL, params=params, timeout=30)
        resp.raise_for_status()
    except requests.RequestException:
        return [], None

    params_list: list[str] = []
    max_end: int | None = None
    for match in INVENTORY_ROW_RE.finditer(resp.text):
        cell1, _cell2, cell3 = match.group(1), match.group(2), match.group(3)
        # Skip header rows
        if cell1.lower() in {"parameter", ""}:
            continue
        params_list.append(cell1.lower())
        years = [int(y) for y in YEAR_RE.findall(cell3)]
        if years:
            year_end = max(years)
            if max_end is None or year_end > max_end:
                max_end = year_end

    return params_list, max_end


def main() -> None:
    db_path = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.environ.get(
            "GAUGE_METADATA_CACHE",
            str(Path(__file__).resolve().parent.parent / "Gauge-metadata-cache" / "gauges.db"),
        )
    )

    session = requests.Session()
    session.headers.update({"User-Agent": "kayak-pn-harvester/1.0"})

    print("Fetching station.js...")
    stations = fetch_station_list(session)
    print(f"  {len(stations)} stations listed")

    print("Fetching decod_params.html (DMS coords, vintage 2006)...")
    decod = fetch_decod_latlon(session)
    print(f"  {len(decod)} stations with coords")

    print("Reading RISE cross-reference from usbr_rise_site table...")
    rise = fetch_rise_latlon(db_path)
    print(f"  {len(rise)} RISE stations with decimal coords")

    now_year = datetime.now(UTC).year
    print(f"Fetching inventory.pl for {len(stations)} stations (concurrent)...")

    def fetch_one(code_name: tuple[str, str]) -> dict:
        code, name = code_name
        # Each worker gets its own Session to avoid cross-thread state on the shared session.
        with requests.Session() as s:
            s.headers.update({"User-Agent": "kayak-pn-harvester/1.0"})
            params_list, last_year = fetch_inventory(s, code)

        if code in rise:
            lat, lon, elev = rise[code]
            source = "rise"
        elif code in decod:
            lat, lon, elev = decod[code]
            source = "decod_params"
        else:
            lat = lon = elev = None
            source = None

        return {
            "code": code,
            "name": name,
            "latitude": lat,
            "longitude": lon,
            "elevation_ft": elev,
            "latlon_source": source,
            "parameters": ",".join(params_list),
            "last_year": last_year,
            "is_active": 1 if (last_year is not None and last_year >= now_year - 1) else 0,
            "fetched_at": datetime.now(UTC).isoformat(),
        }

    rows: list[dict] = []
    items = sorted(stations.items())
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        for i, row in enumerate(pool.map(fetch_one, items), 1):
            rows.append(row)
            if i % 25 == 0:
                print(f"    {i}/{len(items)}", flush=True)

    print(f"  Inventory fetched for {len(rows)} stations")

    conn = sqlite3.connect(db_path)
    conn.execute(CREATE_TABLE)
    conn.executemany(
        """INSERT OR REPLACE INTO pn_hydromet_site (
            code, name, latitude, longitude, elevation_ft, latlon_source,
            parameters, last_year, is_active, fetched_at
        ) VALUES (
            :code, :name, :latitude, :longitude, :elevation_ft, :latlon_source,
            :parameters, :last_year, :is_active, :fetched_at
        )""",
        rows,
    )
    conn.commit()

    # Summary
    active_total = sum(r["is_active"] for r in rows)
    with_coords = sum(1 for r in rows if r["latitude"] is not None)
    print(f"\nStored {len(rows)} stations in {db_path}")
    print(f"  active (last_year >= {now_year - 1}): {active_total}")
    print(f"  with coordinates: {with_coords}")

    cur = conn.execute(
        """SELECT latlon_source, COUNT(*) FROM pn_hydromet_site
            GROUP BY latlon_source ORDER BY COUNT(*) DESC"""
    )
    print("\ncoordinate source breakdown:")
    for src, count in cur:
        print(f"  {src or '(none)'}: {count}")

    conn.close()


if __name__ == "__main__":
    main()
