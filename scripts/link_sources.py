#!/usr/bin/env python3
"""Link source rows to FetchUrl records by extracting station names from URLs.

After import_from_dump.py imports legacy data and init-db adds sources.yaml URLs,
this script bridges the gap: it updates source.fetch_url_id to point to the new
FetchUrl records so the fetch pipeline can map parsed station names to source IDs.

Usage:
    python3 scripts/link_sources.py [--db PATH]
"""

import argparse
import os
import re
import sqlite3


def link_sources(db_path):
    db = sqlite3.connect(db_path)
    cur = db.cursor()

    fetch_urls = cur.execute("SELECT id, url, parser FROM fetch_url").fetchall()

    linked = 0
    skipped = 0

    # NWPS: .../gauges/GIBO3/stageflow/observed
    for fid, url, _parser in fetch_urls:
        if "api.water.noaa.gov/nwps" not in url:
            continue
        m = re.search(r"/gauges/([A-Z0-9]+)/", url)
        if not m:
            continue
        station = m.group(1)
        src = cur.execute("SELECT id FROM source WHERE name = ?", (station,)).fetchone()
        if src:
            cur.execute("UPDATE source SET fetch_url_id = ? WHERE id = ?", (fid, src[0]))
            linked += 1
        else:
            skipped += 1

    # WA Ecology: .../Prod/29C100/29C100_STG_FM.TXT
    for fid, url, _parser in fetch_urls:
        if "apps.ecology.wa.gov" not in url:
            continue
        m = re.search(r"/Prod/([A-Z0-9]+)/", url)
        if not m:
            continue
        station = m.group(1)
        src = cur.execute("SELECT id FROM source WHERE name = ?", (station,)).fetchone()
        if src:
            cur.execute("UPDATE source SET fetch_url_id = ? WHERE id = ?", (fid, src[0]))
            linked += 1

    # USBR: station codes in the 'list' query parameter
    for fid, url, _parser in fetch_urls:
        if "usbr.gov" not in url:
            continue
        m = re.search(r"list=([A-Z0-9,]+)", url)
        if not m:
            continue
        for station in m.group(1).split(","):
            src = cur.execute("SELECT id FROM source WHERE name = ?", (station,)).fetchone()
            if src:
                cur.execute("UPDATE source SET fetch_url_id = ? WHERE id = ?", (fid, src[0]))
                linked += 1

    # USACE CDA: station codes like GPR, HCR in JSON query
    for fid, url, _parser in fetch_urls:
        if "usace.army.mil" not in url:
            continue
        for station in set(re.findall(r'"([A-Z]{3})\.[A-Za-z-]+', url)):
            src = cur.execute("SELECT id FROM source WHERE name = ?", (station,)).fetchone()
            if src:
                cur.execute("UPDATE source SET fetch_url_id = ? WHERE id = ?", (fid, src[0]))
                linked += 1

    # USGS Water Services: remap sources from old per-state URLs to new ones.
    # Old: https://waterdata.usgs.gov/az/nwis/current/?format=rdb
    # New: https://waterservices.usgs.gov/nwis/iv/?format=rdb&stateCd=az&...
    # Build mapping: state code -> new fetch_url id
    new_usgs = {}
    for fid, url, _parser in fetch_urls:
        if "waterservices.usgs.gov" not in url:
            continue
        m = re.search(r"stateCd=([a-z]{2})", url)
        if m:
            new_usgs[m.group(1)] = fid

    if new_usgs:
        # Find old USGS fetch_urls and remap their sources
        old_usgs = {}
        for fid, url, _parser in fetch_urls:
            if "waterdata.usgs.gov" not in url or parser != "usgs":
                continue
            m = re.search(r"waterdata\.usgs\.gov/([a-z]{2})/", url)
            if m:
                old_usgs[fid] = m.group(1)

        for old_fid, state_cd in old_usgs.items():
            new_fid = new_usgs.get(state_cd)
            if new_fid:
                count = cur.execute(
                    "UPDATE source SET fetch_url_id = ? WHERE fetch_url_id = ?",
                    (new_fid, old_fid),
                ).rowcount
                linked += count

    db.commit()
    print(f"  Linked {linked} sources to fetch_urls ({skipped} stations not in source table)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Link sources to fetch_url records")
    parser.add_argument(
        "--db",
        default=os.path.join(os.path.dirname(__file__), "..", "..", "DB", "kayak.db"),
        help="SQLite database path",
    )
    args = parser.parse_args()
    link_sources(args.db)
