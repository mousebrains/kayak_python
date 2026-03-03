#!/usr/bin/env python3
"""
Import production data from a MySQL dump of levels_todo into local SQLite.

Based on the migration scripts in ~/tpw/kayak_new/migrate/, adapted to parse
a mysqldump file rather than query a live MySQL database.

Usage:
    python3 scripts/import_from_dump.py [--dump PATH] [--db PATH]

Defaults:
    --dump  ~/tpw/kayak_new/current.sql
    --db    ./kayak.db
"""

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

# Add src/ to path so we can import kayak models
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from kayak.db.models import Base

logging.basicConfig(
    format="%(asctime)s %(levelname)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enum mappings from legacy int IDs to new string values
# ---------------------------------------------------------------------------

DATATYPE_MAP = {1: "gauge", 2: "flow", 3: "inflow", 4: "temperature"}
LEVEL_MAP = {1: "low", 2: "okay", 3: "high"}


# ---------------------------------------------------------------------------
# MySQL dump parser
# ---------------------------------------------------------------------------

def parse_mysql_values(values_str):
    """Parse a MySQL VALUES clause into a list of tuples.

    Handles quoted strings with escaped characters, NULL, and numbers.
    Returns list of tuples of Python values.
    """
    rows = []
    i = 0
    n = len(values_str)

    while i < n:
        # Find start of a row
        while i < n and values_str[i] != '(':
            i += 1
        if i >= n:
            break
        i += 1  # skip '('

        row = []
        while i < n and values_str[i] != ')':
            # Skip whitespace
            while i < n and values_str[i] in (' ', '\t'):
                i += 1

            if values_str[i] == "'":
                # Quoted string
                i += 1
                parts = []
                while i < n:
                    if values_str[i] == '\\' and i + 1 < n:
                        next_ch = values_str[i + 1]
                        if next_ch == "'":
                            parts.append("'")
                        elif next_ch == '"':
                            parts.append('"')
                        elif next_ch == '\\':
                            parts.append('\\')
                        elif next_ch == 'n':
                            parts.append('\n')
                        elif next_ch == 'r':
                            parts.append('\r')
                        elif next_ch == 't':
                            parts.append('\t')
                        elif next_ch == '0':
                            parts.append('\0')
                        else:
                            parts.append(next_ch)
                        i += 2
                    elif values_str[i] == "'":
                        i += 1
                        break
                    else:
                        parts.append(values_str[i])
                        i += 1
                row.append(''.join(parts))
            elif values_str[i:i+4] == 'NULL':
                row.append(None)
                i += 4
            else:
                # Number or other literal
                start = i
                while i < n and values_str[i] not in (',', ')'):
                    i += 1
                val = values_str[start:i].strip()
                # Try to convert to number
                try:
                    if '.' in val:
                        row.append(float(val))
                    else:
                        row.append(int(val))
                except ValueError:
                    row.append(val)

            # Skip comma between values
            while i < n and values_str[i] in (' ', '\t'):
                i += 1
            if i < n and values_str[i] == ',':
                i += 1

        if i < n and values_str[i] == ')':
            i += 1
        rows.append(tuple(row))

        # Skip comma between rows or semicolon at end
        while i < n and values_str[i] in (',', ' ', '\t', '\n', '\r', ';'):
            i += 1

    return rows


def extract_table_data(dump_path, table_name):
    """Extract all INSERT rows for a given table from the dump file.

    Reads the file line by line to handle the 571MB dump efficiently.
    Returns a list of tuples.
    """
    prefix = f"INSERT INTO `{table_name}` VALUES "
    rows = []
    collecting = False
    buffer = []

    with open(dump_path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            if line.startswith(prefix):
                values_part = line[len(prefix):]
                rows.extend(parse_mysql_values(values_part))
            # Stop early if we've passed the table's section
            elif rows and line.startswith("UNLOCK TABLES"):
                break

    return rows


def extract_all_timeseries_tables(dump_path):
    """Extract all per-gauge timeseries tables (flow_*, gauge_*, etc.) and Latest.

    Returns dict: table_name -> list of (time_str, value_str) tuples.
    """
    known_prefixes = ("flow_", "gauge_", "temperature_", "inflow_", "GotMe_")
    tables = {}
    current_table = None

    with open(dump_path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            if line.startswith("INSERT INTO `"):
                # Extract table name
                m = re.match(r"INSERT INTO `([^`]+)` VALUES ", line)
                if not m:
                    continue
                tname = m.group(1)

                if tname == "Latest" or any(tname.startswith(p) for p in known_prefixes):
                    prefix = f"INSERT INTO `{tname}` VALUES "
                    values_part = line[len(prefix):]
                    parsed = parse_mysql_values(values_part)
                    if tname not in tables:
                        tables[tname] = []
                    tables[tname].extend(parsed)

    return tables


# ---------------------------------------------------------------------------
# Import steps (mirrors ~/tpw/kayak_new/migrate/ scripts)
# ---------------------------------------------------------------------------

def import_states(session, dump_path):
    """Step 1a: Import states."""
    log.info("--- Importing states ---")
    rows = extract_table_data(dump_path, "state")
    for row in rows:
        # state: (id, abbreviation, full_name) in new dump format
        # or (id, name) in old format - check columns
        if len(row) >= 3:
            sid, abbr, name = row[0], row[1], row[2]
        else:
            sid, name = row[0], row[1]
            abbr = name  # abbreviation = name in old format
        session.execute(text(
            "INSERT OR IGNORE INTO state (id, name, abbreviation) "
            "VALUES (:id, :name, :abbr)"
        ), {"id": sid, "name": name, "abbr": abbr})
    session.commit()
    log.info("  Imported %d states", len(rows))


def import_class_descriptions(session, dump_path):
    """Step 1b: Import class descriptions."""
    log.info("--- Importing class descriptions ---")
    rows = extract_table_data(dump_path, "classDescription")
    for row in rows:
        name, desc = row[0], row[1]
        session.execute(text(
            "INSERT OR IGNORE INTO class_description (name, description) "
            "VALUES (:name, :desc)"
        ), {"name": name, "desc": desc})
    session.commit()
    log.info("  Imported %d class descriptions", len(rows))


def import_guidebooks(session, dump_path):
    """Step 1c: Import guidebooks."""
    log.info("--- Importing guidebooks ---")
    rows = extract_table_data(dump_path, "guideBook")
    for row in rows:
        # guideBook: (id, title, subTitle, edition, author, url)
        gid, title = row[0], row[1]
        subtitle = row[2] if len(row) > 2 else None
        edition = row[3] if len(row) > 3 else None
        author = row[4] if len(row) > 4 else None
        url = row[5] if len(row) > 5 else None
        session.execute(text(
            "INSERT OR IGNORE INTO guidebook (id, title, subtitle, edition, author, url) "
            "VALUES (:id, :title, :subtitle, :edition, :author, :url)"
        ), {"id": gid, "title": title, "subtitle": subtitle,
            "edition": edition, "author": author, "url": url})
    session.commit()
    log.info("  Imported %d guidebooks", len(rows))


def import_ratings(session, dump_path):
    """Step 2a: Import rating definitions."""
    log.info("--- Importing ratings ---")
    rows = extract_table_data(dump_path, "rating")
    for row in rows:
        # rating: (id, url, parser)
        session.execute(text(
            "INSERT OR IGNORE INTO rating (id, url, parser) "
            "VALUES (:id, :url, :parser)"
        ), {"id": row[0], "url": row[1], "parser": row[2] if len(row) > 2 else None})
    session.commit()
    log.info("  Imported %d ratings", len(rows))


def import_gauges(session, dump_path):
    """Step 2b: Import gauges, renaming camelCase to snake_case."""
    log.info("--- Importing gauges ---")
    rows = extract_table_data(dump_path, "gauge")
    for row in rows:
        # gauge: (id, name, bankFull, floodStage, location, latitude, longitude,
        #         stationID, cbttID, geosID, nwsID, nwsliID, snotelID, usgsID, rating)
        session.execute(text(
            "INSERT OR IGNORE INTO gauge "
            "(id, name, bank_full, flood_stage, location, latitude, longitude, "
            " station_id, cbtt_id, geos_id, nws_id, nwsli_id, snotel_id, usgs_id, rating_id) "
            "VALUES (:id, :name, :bank_full, :flood_stage, :location, :lat, :lon, "
            " :station_id, :cbtt_id, :geos_id, :nws_id, :nwsli_id, :snotel_id, :usgs_id, :rating_id)"
        ), {
            "id": row[0], "name": row[1],
            "bank_full": row[2], "flood_stage": row[3],
            "location": row[4], "lat": row[5], "lon": row[6],
            "station_id": row[7], "cbtt_id": row[8], "geos_id": row[9],
            "nws_id": row[10], "nwsli_id": row[11], "snotel_id": row[12],
            "usgs_id": row[13],
            "rating_id": row[14] if len(row) > 14 and row[14] else None,
        })
    session.commit()
    log.info("  Imported %d gauges", len(rows))


def import_fetch_urls(session, dump_path):
    """Step 2c: Import URL definitions."""
    log.info("--- Importing fetch_url ---")
    rows = extract_table_data(dump_path, "url")
    for row in rows:
        # url: (id, url, parser, hours, qFetch, t)
        hours_val = row[3] if len(row) > 3 else None
        is_active = row[4] if len(row) > 4 else 1
        last_fetched = row[5] if len(row) > 5 else None
        session.execute(text(
            "INSERT OR IGNORE INTO fetch_url (id, url, parser, hours, is_active, last_fetched_at) "
            "VALUES (:id, :url, :parser, :hours, :is_active, :last_fetched)"
        ), {
            "id": row[0], "url": row[1], "parser": row[2],
            "hours": hours_val, "is_active": 1 if is_active else 0,
            "last_fetched": last_fetched,
        })
    session.commit()
    log.info("  Imported %d fetch_url rows", len(rows))


def import_calc_expressions(session, dump_path):
    """Step 2d: Import calc expressions, mapping int dataType to enum string."""
    log.info("--- Importing calc_expression ---")
    rows = extract_table_data(dump_path, "calc")
    for row in rows:
        # calc: (id, dataType, expr, time, note)
        dt_str = DATATYPE_MAP.get(row[1])
        if dt_str is None:
            log.warning("  Unknown dataType %s for calc id %s, skipping", row[1], row[0])
            continue
        session.execute(text(
            "INSERT OR IGNORE INTO calc_expression (id, data_type, expression, time_expression, note) "
            "VALUES (:id, :dt, :expr, :time_expr, :note)"
        ), {
            "id": row[0], "dt": dt_str, "expr": row[2],
            "time_expr": row[3] if len(row) > 3 else None,
            "note": row[4] if len(row) > 4 else None,
        })
    session.commit()
    log.info("  Imported %d calc expressions", len(rows))


def import_sources(session, dump_path):
    """Step 2e: Import sources, mapping url/calc int FKs."""
    log.info("--- Importing sources ---")
    rows = extract_table_data(dump_path, "source")
    count = 0
    for row in rows:
        # source: (id, url, calc, name, agency)
        url_id = row[1] if row[1] and row[1] != 0 else None
        calc_id = row[2] if row[2] and row[2] != 0 else None
        if url_id is None and calc_id is None:
            continue
        session.execute(text(
            "INSERT OR IGNORE INTO source (id, name, agency, fetch_url_id, calc_expression_id) "
            "VALUES (:id, :name, :agency, :url_id, :calc_id)"
        ), {
            "id": row[0], "name": row[3],
            "agency": row[4] if len(row) > 4 else None,
            "url_id": url_id, "calc_id": calc_id,
        })
        count += 1
    session.commit()
    log.info("  Imported %d sources (skipped %d without url/calc)", count, len(rows) - count)


def import_gauge_source(session, dump_path):
    """Step 2f: Import gauge-to-source junction table."""
    log.info("--- Importing gauge_source ---")
    rows = extract_table_data(dump_path, "gauge2source")
    count = 0
    for row in rows:
        # gauge2source: (gauge, src)
        try:
            session.execute(text(
                "INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) "
                "VALUES (:gid, :sid)"
            ), {"gid": row[0], "sid": row[1]})
            count += 1
        except Exception:
            pass  # FK violation — source or gauge doesn't exist
    session.commit()
    log.info("  Imported %d gauge_source rows", count)


def import_reaches(session, dump_path):
    """Step 3a: Import reaches, renaming camelCase to snake_case."""
    log.info("--- Importing reaches ---")
    rows = extract_table_data(dump_path, "section")
    for row in rows:
        # section: (id, tUpdate, gauge, name, displayName, sortName, nature,
        #   description, difficulties, basin, basinArea, elevation, elevationLost,
        #   length, gradient, features, latitude, longitude,
        #   latitudeStart, longitudeStart, latitudeEnd, longitudeEnd,
        #   mapName, noShow, notes, optimalFlow, region, remoteness, scenery,
        #   season, watershedType, awID)
        gauge_id = row[2] if row[2] and row[2] != 0 else None
        session.execute(text(
            "INSERT OR IGNORE INTO reach "
            "(id, updated_at, gauge_id, name, display_name, sort_name, nature, "
            " description, difficulties, basin, basin_area, elevation, elevation_lost, "
            " length, gradient, features, latitude, longitude, "
            " latitude_start, longitude_start, latitude_end, longitude_end, "
            " map_name, no_show, notes, optimal_flow, region, remoteness, scenery, "
            " season, watershed_type, aw_id) "
            "VALUES (:id, :updated_at, :gauge_id, :name, :display_name, :sort_name, :nature, "
            " :description, :difficulties, :basin, :basin_area, :elevation, :elevation_lost, "
            " :length, :gradient, :features, :latitude, :longitude, "
            " :lat_start, :lon_start, :lat_end, :lon_end, "
            " :map_name, :no_show, :notes, :optimal_flow, :region, :remoteness, :scenery, "
            " :season, :watershed_type, :aw_id)"
        ), {
            "id": row[0], "updated_at": row[1], "gauge_id": gauge_id,
            "name": row[3], "display_name": row[4], "sort_name": row[5],
            "nature": row[6], "description": row[7], "difficulties": row[8],
            "basin": row[9], "basin_area": row[10], "elevation": row[11],
            "elevation_lost": row[12], "length": row[13], "gradient": row[14],
            "features": row[15], "latitude": row[16], "longitude": row[17],
            "lat_start": row[18], "lon_start": row[19],
            "lat_end": row[20], "lon_end": row[21],
            "map_name": row[22], "no_show": 1 if row[23] else 0,
            "notes": row[24], "optimal_flow": row[25],
            "region": row[26], "remoteness": row[27], "scenery": row[28],
            "season": row[29],
            "watershed_type": row[30] if len(row) > 30 else None,
            "aw_id": row[31] if len(row) > 31 else None,
        })
    session.commit()
    log.info("  Imported %d reaches", len(rows))


def import_reach_state(session, dump_path):
    """Step 3b: Import reach-to-state junction."""
    log.info("--- Importing reach_state ---")
    rows = extract_table_data(dump_path, "section2state")
    count = 0
    for row in rows:
        # section2state: (section, state)
        try:
            session.execute(text(
                "INSERT OR IGNORE INTO reach_state (reach_id, state_id) "
                "VALUES (:sec, :st)"
            ), {"sec": row[0], "st": row[1]})
            count += 1
        except Exception:
            pass
    session.commit()
    log.info("  Imported %d reach_state rows", count)


def import_reach_class(session, dump_path):
    """Step 3c: Import reach classes, mapping int dataType to enum string."""
    log.info("--- Importing reach_class ---")
    rows = extract_table_data(dump_path, "class")
    count = 0
    for row in rows:
        # class: (section, name, low, lowDatatype, high, highDatatype)
        low_dt = DATATYPE_MAP.get(row[3])
        high_dt = DATATYPE_MAP.get(row[5])
        if low_dt is None or high_dt is None:
            continue
        try:
            session.execute(text(
                "INSERT OR IGNORE INTO reach_class "
                "(reach_id, name, low, low_data_type, high, high_data_type) "
                "VALUES (:sec, :name, :low, :low_dt, :high, :high_dt)"
            ), {
                "sec": row[0], "name": row[1],
                "low": row[2], "low_dt": low_dt,
                "high": row[4], "high_dt": high_dt,
            })
            count += 1
        except Exception:
            pass
    session.commit()
    log.info("  Imported %d reach_class rows", count)


def import_reach_level(session, dump_path):
    """Step 3d: Import reach levels, mapping int level/dataType to enum strings."""
    log.info("--- Importing reach_level ---")
    # Try section2level first, fall back to section2levels
    rows = extract_table_data(dump_path, "section2level")
    if not rows:
        rows = extract_table_data(dump_path, "section2levels")
    count = 0
    for row in rows:
        # section2level: (section, level, low, lowDatatype, high, highDatatype)
        level_str = LEVEL_MAP.get(row[1])
        low_dt = DATATYPE_MAP.get(row[3])
        high_dt = DATATYPE_MAP.get(row[5])
        if level_str is None or low_dt is None or high_dt is None:
            continue
        try:
            session.execute(text(
                "INSERT OR IGNORE INTO reach_level "
                "(reach_id, level, low, low_data_type, high, high_data_type) "
                "VALUES (:sec, :level, :low, :low_dt, :high, :high_dt)"
            ), {
                "sec": row[0], "level": level_str,
                "low": row[2], "low_dt": low_dt,
                "high": row[4], "high_dt": high_dt,
            })
            count += 1
        except Exception:
            pass
    session.commit()
    log.info("  Imported %d reach_level rows", count)


def import_reach_guidebook(session, dump_path):
    """Step 3e: Import reach-to-guidebook links."""
    log.info("--- Importing reach_guidebook ---")
    rows = extract_table_data(dump_path, "section2GuideBook")
    count = 0
    for row in rows:
        # section2GuideBook: (section, guideBook, page, run, url)
        try:
            session.execute(text(
                "INSERT OR IGNORE INTO reach_guidebook "
                "(reach_id, guidebook_id, page, run, url) "
                "VALUES (:sec, :gb, :page, :run, :url)"
            ), {
                "sec": row[0], "gb": row[1],
                "page": row[2] if len(row) > 2 else None,
                "run": row[3] if len(row) > 3 else None,
                "url": row[4] if len(row) > 4 else None,
            })
            count += 1
        except Exception:
            pass
    session.commit()
    log.info("  Imported %d reach_guidebook rows", count)


def import_rating_data(session, dump_path):
    """Step 3f: Import rating curve data points."""
    log.info("--- Importing rating_data ---")
    rows = extract_table_data(dump_path, "ratingData")
    for row in rows:
        # ratingData: (rating, gauge, flow)
        try:
            session.execute(text(
                "INSERT OR IGNORE INTO rating_data (rating_id, gauge_height_ft, flow_cfs) "
                "VALUES (:rid, :gh, :flow)"
            ), {"rid": row[0], "gh": row[1], "flow": row[2]})
        except Exception:
            pass
    session.commit()
    log.info("  Imported %d rating_data rows", len(rows))


def import_timeseries(session, dump_path):
    """Step 4: Import time-series data from levels_todo.data table.

    Maps int dataType FK to enum string via DATATYPE_MAP.
    """
    log.info("--- Importing observation data (levels_todo.data) ---")
    log.info("  This is the largest table — may take a few minutes...")

    # Build set of valid source IDs
    result = session.execute(text("SELECT id FROM source"))
    valid_sources = {row[0] for row in result}
    log.info("  %d valid sources in database", len(valid_sources))

    # Process the dump file line by line for the data table
    prefix = "INSERT INTO `data` VALUES "
    total = 0
    skipped = 0
    batch = []
    BATCH_SIZE = 10000

    start = time.time()
    with open(dump_path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            if not line.startswith(prefix):
                if total > 0 and line.startswith("UNLOCK TABLES"):
                    break
                continue

            values_part = line[len(prefix):]
            rows = parse_mysql_values(values_part)

            for row in rows:
                # data: (src, dataType, value, t)
                src_id = row[0]
                if src_id not in valid_sources:
                    skipped += 1
                    continue
                dt_str = DATATYPE_MAP.get(row[1])
                if dt_str is None:
                    skipped += 1
                    continue

                batch.append({
                    "src": src_id, "dt": dt_str,
                    "val": row[2], "t": row[3],
                })
                total += 1

                if len(batch) >= BATCH_SIZE:
                    session.execute(text(
                        "INSERT OR IGNORE INTO observation "
                        "(source_id, data_type, value, observed_at) "
                        "VALUES (:src, :dt, :val, :t)"
                    ), batch)
                    batch = []
                    if total % 500000 == 0:
                        session.commit()
                        elapsed = time.time() - start
                        log.info("  %d rows (%.0fs, %d skipped)...",
                                 total, elapsed, skipped)

    if batch:
        session.execute(text(
            "INSERT OR IGNORE INTO observation "
            "(source_id, data_type, value, observed_at) "
            "VALUES (:src, :dt, :val, :t)"
        ), batch)
    session.commit()

    elapsed = time.time() - start
    log.info("  Imported %d observations in %.0fs (skipped %d)", total, elapsed, skipped)


def import_per_gauge_tables(session, dump_path):
    """Step 4b: Import per-gauge tables (flow_*, gauge_*, temperature_*, inflow_*).

    Each table name encodes the data type and source name.
    """
    log.info("--- Importing per-gauge timeseries tables ---")
    log.info("  Scanning dump for per-gauge tables (this may take a while)...")

    # Build source name -> id mapping
    result = session.execute(text("SELECT name, id FROM source"))
    name_to_src = {row[0]: row[1] for row in result}

    known_prefixes = {"flow", "gauge", "temperature", "inflow"}
    total_rows = 0
    total_tables = 0
    skipped_tables = 0

    prefix_re = re.compile(r"INSERT INTO `((?:flow|gauge|temperature|inflow)_[^`]+)` VALUES ")

    start = time.time()
    batch = []
    BATCH_SIZE = 10000
    current_table = None
    current_src_id = None
    current_dt = None

    with open(dump_path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            m = prefix_re.match(line)
            if not m:
                continue

            tname = m.group(1)
            parts = tname.split("_", 1)
            if len(parts) != 2:
                continue

            data_type = parts[0]
            source_name = parts[1]

            if source_name not in name_to_src:
                skipped_tables += 1
                continue

            src_id = name_to_src[source_name]

            if tname != current_table:
                current_table = tname
                total_tables += 1

            prefix = f"INSERT INTO `{tname}` VALUES "
            values_part = line[len(prefix):]
            rows = parse_mysql_values(values_part)

            for row in rows:
                # per-gauge table: (time, value)
                ts = row[0]
                val_str = row[1]
                if val_str is None or val_str == '':
                    continue
                try:
                    val = float(val_str) if isinstance(val_str, str) else val_str
                except (ValueError, TypeError):
                    continue

                batch.append({
                    "src": src_id, "dt": data_type,
                    "val": val, "t": ts,
                })
                total_rows += 1

                if len(batch) >= BATCH_SIZE:
                    session.execute(text(
                        "INSERT OR IGNORE INTO observation "
                        "(source_id, data_type, value, observed_at) "
                        "VALUES (:src, :dt, :val, :t)"
                    ), batch)
                    batch = []

                    if total_rows % 500000 == 0:
                        session.commit()
                        elapsed = time.time() - start
                        log.info("  %d rows from %d tables (%.0fs)...",
                                 total_rows, total_tables, elapsed)

    if batch:
        session.execute(text(
            "INSERT OR IGNORE INTO observation "
            "(source_id, data_type, value, observed_at) "
            "VALUES (:src, :dt, :val, :t)"
        ), batch)
    session.commit()

    elapsed = time.time() - start
    log.info("  Imported %d rows from %d tables in %.0fs (skipped %d tables)",
             total_rows, total_tables, elapsed, skipped_tables)


def import_latest(session, dump_path):
    """Step 5: Import Latest table into latest_observation cache."""
    log.info("--- Importing latest_observation ---")

    # Build source name -> id mapping
    result = session.execute(text("SELECT name, id FROM source"))
    name_to_src = {row[0]: row[1] for row in result}

    rows = extract_table_data(dump_path, "Latest")
    known_prefixes = {"flow", "gauge", "temperature", "inflow"}
    count = 0

    for row in rows:
        # Latest: (name, time, value, prevTime, prevValue, delta)
        name = row[0]
        parts = name.split("_", 1)
        if len(parts) != 2 or parts[0] not in known_prefixes:
            continue

        data_type = parts[0]
        source_name = parts[1]

        if source_name not in name_to_src:
            continue

        src_id = name_to_src[source_name]

        obs_time = row[1]
        value = row[2]
        prev_time = row[3] if len(row) > 3 else None
        prev_value = row[4] if len(row) > 4 else None
        delta = row[5] if len(row) > 5 else None

        if obs_time is None or value is None:
            continue

        try:
            session.execute(text(
                "INSERT OR IGNORE INTO latest_observation "
                "(source_id, data_type, observed_at, value, "
                " prev_observed_at, prev_value, delta_per_hour) "
                "VALUES (:src, :dt, :obs_time, :value, "
                " :prev_time, :prev_value, :delta)"
            ), {
                "src": src_id, "dt": data_type,
                "obs_time": obs_time, "value": value,
                "prev_time": prev_time, "prev_value": prev_value,
                "delta": delta,
            })
            count += 1
        except Exception:
            pass

    session.commit()
    log.info("  Imported %d latest_observation rows", count)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Import production MySQL dump into local SQLite"
    )
    parser.add_argument(
        "--dump", default=os.path.expanduser("~/tpw/kayak_new/current.sql"),
        help="Path to MySQL dump file (default: ~/tpw/kayak_new/current.sql)"
    )
    parser.add_argument(
        "--db", default="kayak.db",
        help="Path to SQLite database (default: ./kayak.db)"
    )
    parser.add_argument(
        "--skip-timeseries", action="store_true",
        help="Skip importing observation data (metadata only)"
    )
    parser.add_argument(
        "--skip-per-gauge", action="store_true",
        help="Skip importing per-gauge tables (only import levels_todo.data)"
    )
    args = parser.parse_args()

    dump_path = args.dump
    if not os.path.exists(dump_path):
        log.error("Dump file not found: %s", dump_path)
        sys.exit(1)

    db_path = args.db
    db_url = f"sqlite:///{os.path.abspath(db_path)}"

    # Remove existing DB to start fresh
    if os.path.exists(db_path):
        os.remove(db_path)
        log.info("Removed existing %s", db_path)

    log.info("Creating SQLite database: %s", db_path)
    log.info("Reading from: %s", dump_path)

    engine = create_engine(db_url, echo=False)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=OFF")  # OFF during import for speed
        cursor.execute("PRAGMA synchronous=OFF")
        cursor.execute("PRAGMA cache_size=-512000")  # 512MB cache
        cursor.close()

    # Create all tables
    Base.metadata.create_all(engine)
    log.info("Created schema (%d tables)", len(Base.metadata.tables))

    overall_start = time.time()

    with Session(engine) as session:
        # Step 1: Reference data
        import_states(session, dump_path)
        import_class_descriptions(session, dump_path)
        import_guidebooks(session, dump_path)

        # Step 2: Gauge/source/rating infrastructure
        import_ratings(session, dump_path)
        import_gauges(session, dump_path)
        import_fetch_urls(session, dump_path)
        import_calc_expressions(session, dump_path)
        import_sources(session, dump_path)
        import_gauge_source(session, dump_path)

        # Step 3: Reaches and junctions
        import_reaches(session, dump_path)
        import_reach_state(session, dump_path)
        import_reach_class(session, dump_path)
        import_reach_level(session, dump_path)
        import_reach_guidebook(session, dump_path)
        import_rating_data(session, dump_path)

        # Step 4: Time-series data
        if not args.skip_timeseries:
            import_timeseries(session, dump_path)
            if not args.skip_per_gauge:
                import_per_gauge_tables(session, dump_path)

        # Step 5: Latest observation cache
        import_latest(session, dump_path)

    # Re-enable foreign keys and verify
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))

    elapsed = time.time() - overall_start
    db_size = os.path.getsize(db_path) / (1024 * 1024)
    log.info("=== Done in %.0fs. Database: %s (%.1f MB) ===", elapsed, db_path, db_size)

    # Print summary counts
    with Session(engine) as session:
        for table_name in [
            "state", "class_description", "guidebook", "rating", "gauge",
            "fetch_url", "calc_expression", "source", "gauge_source",
            "reach", "reach_state", "reach_class", "reach_level",
            "reach_guidebook", "rating_data", "observation", "latest_observation",
        ]:
            count = session.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()
            log.info("  %-25s %8d rows", table_name, count)


if __name__ == "__main__":
    main()
