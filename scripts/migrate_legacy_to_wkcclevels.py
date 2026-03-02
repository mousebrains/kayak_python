#!/usr/bin/env python3
"""
Migrate legacy MySQL databases into `wkcclevels` with the new normalized schema.

Reads from (read-only):
  - levels_todo (gauge, source, URL, calc, rating, section, state, etc.)
  - levels_data (per-station flow_*/gage_*/temperature_* tables + Latest)
  - levels_page (Pages table)

Writes to:
  - wkcclevels (new schema with snake_case columns, string enums)

Usage:
    # Dry run (show what would be migrated)
    python3 scripts/migrate_legacy_to_wkcclevels.py --dry-run

    # Full migration (metadata + observations)
    python3 scripts/migrate_legacy_to_wkcclevels.py

    # Metadata only (skip observation data)
    python3 scripts/migrate_legacy_to_wkcclevels.py --skip-observations

    # Only last 30 days of observations
    python3 scripts/migrate_legacy_to_wkcclevels.py --days 30

    # Via SSH tunnel
    ssh -L 3307:mysql.wkcc.dreamhosters.com:3306 tpw@levels.wkcc.org -N &
    python3 scripts/migrate_legacy_to_wkcclevels.py \
        --legacy-host 127.0.0.1 --legacy-port 3307
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import pymysql  # noqa: F401 — needed for mysql+pymysql:// URLs
except ImportError:
    pass  # Will fail at runtime when connecting to MySQL
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

# Add src/ to path so we can import kayak modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

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

# Legacy table prefix → new DataType string
LEGACY_PREFIXES = {
    "flow": "flow",
    "gage": "gauge",
    "gauge": "gauge",
    "temperature": "temperature",
    "inflow": "inflow",
}

# Default MySQL connection params
DEFAULT_HOST = "mysql.wkcc.dreamhosters.com"
DEFAULT_PORT = 3306
DEFAULT_USER = "levels"
DEFAULT_PASS = "Deschutes"


def make_url(host, port, user, password, db):
    """Build a mysql+pymysql:// URL."""
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{db}"


# ---------------------------------------------------------------------------
# MySQL upsert helper
# ---------------------------------------------------------------------------

def mysql_upsert(session, table, columns, rows, update_cols=None):
    """Batch upsert rows into a MySQL table.

    columns: list of column names
    rows: list of dicts with keys matching columns
    update_cols: columns to update on duplicate key (default: INSERT IGNORE)
    """
    if not rows:
        return
    placeholders = ", ".join(f":{c}" for c in columns)
    col_list = ", ".join(columns)
    if update_cols:
        update_clause = ", ".join(f"{c} = VALUES({c})" for c in update_cols)
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON DUPLICATE KEY UPDATE {update_clause}"
        )
    else:
        sql = f"INSERT IGNORE INTO {table} ({col_list}) VALUES ({placeholders})"
    session.execute(text(sql), rows)
    session.flush()


# ---------------------------------------------------------------------------
# Metadata migration (levels_todo → wkcclevels)
# ---------------------------------------------------------------------------

def migrate_states(src, tgt, dry_run):
    """Migrate state table."""
    log.info("--- Migrating states ---")
    try:
        rows = src.execute(text("SELECT id, short, name FROM state")).fetchall()
    except Exception:
        try:
            rows = src.execute(text("SELECT id, abbreviation, full_name FROM state")).fetchall()
        except Exception:
            rows = src.execute(text("SELECT id, abbreviation, name FROM state")).fetchall()

    if dry_run:
        log.info("  [DRY RUN] %d rows", len(rows))
        return len(rows)

    batch = [{"id": r[0], "abbreviation": r[1], "name": r[2]} for r in rows]
    mysql_upsert(tgt, "state", ["id", "name", "abbreviation"], batch)
    tgt.commit()
    log.info("  Wrote %d states", len(rows))
    return len(rows)


def migrate_class_descriptions(src, tgt, dry_run):
    """Migrate classDescription → class_description."""
    log.info("--- Migrating class_description ---")
    rows = src.execute(text("SELECT name, description FROM classDescription")).fetchall()

    if dry_run:
        log.info("  [DRY RUN] %d rows", len(rows))
        return len(rows)

    batch = [{"name": r[0], "description": r[1]} for r in rows]
    mysql_upsert(tgt, "class_description", ["name", "description"], batch)
    tgt.commit()
    log.info("  Wrote %d class_description rows", len(rows))
    return len(rows)


def migrate_guidebooks(src, tgt, dry_run):
    """Migrate guideBook → guidebook."""
    log.info("--- Migrating guidebooks ---")
    rows = src.execute(
        text("SELECT id, title, subTitle, edition, author, url FROM guideBook")
    ).fetchall()

    if dry_run:
        log.info("  [DRY RUN] %d rows", len(rows))
        return len(rows)

    batch = [{
        "id": r[0], "title": r[1], "subtitle": r[2],
        "edition": r[3], "author": r[4], "url": r[5],
    } for r in rows]
    mysql_upsert(tgt, "guidebook",
                 ["id", "title", "subtitle", "edition", "author", "url"], batch)
    tgt.commit()
    log.info("  Wrote %d guidebooks", len(rows))
    return len(rows)


def migrate_ratings(src, tgt, dry_run):
    """Migrate rating table."""
    log.info("--- Migrating ratings ---")
    rows = src.execute(text("SELECT id, url, parser FROM rating")).fetchall()

    if dry_run:
        log.info("  [DRY RUN] %d rows", len(rows))
        return len(rows)

    batch = [{"id": r[0], "url": r[1], "parser": r[2]} for r in rows]
    mysql_upsert(tgt, "rating", ["id", "url", "parser"], batch)
    tgt.commit()
    log.info("  Wrote %d ratings", len(rows))
    return len(rows)


def migrate_rating_data(src, tgt, dry_run):
    """Migrate ratingData → rating_data."""
    log.info("--- Migrating rating_data ---")
    try:
        rows = src.execute(text("SELECT rating, gauge, flow FROM ratingData")).fetchall()
    except Exception:
        rows = []

    if dry_run:
        log.info("  [DRY RUN] %d rows", len(rows))
        return len(rows)

    if not rows:
        log.info("  No rating_data rows (table empty)")
        return 0

    batch = [{"rating_id": r[0], "gauge_height_ft": r[1], "flow_cfs": r[2]} for r in rows]
    mysql_upsert(tgt, "rating_data",
                 ["rating_id", "gauge_height_ft", "flow_cfs"], batch)
    tgt.commit()
    log.info("  Wrote %d rating_data rows", len(rows))
    return len(rows)


def migrate_fetch_urls(src, tgt, dry_run):
    """Migrate url/URL → fetch_url."""
    log.info("--- Migrating fetch_url ---")
    try:
        rows = src.execute(
            text("SELECT id, url, parser, hours, qFetch, t FROM url")
        ).fetchall()
    except Exception:
        rows = src.execute(
            text("SELECT id, url, parser, hours, qFetch, t FROM URL")
        ).fetchall()

    if dry_run:
        log.info("  [DRY RUN] %d rows", len(rows))
        return len(rows)

    batch = [{
        "id": r[0], "url": r[1], "parser": r[2],
        "hours": r[3], "is_active": 1 if r[4] else 0,
        "last_fetched_at": r[5],
    } for r in rows]
    mysql_upsert(tgt, "fetch_url",
                 ["id", "url", "parser", "hours", "is_active", "last_fetched_at"], batch)
    tgt.commit()
    log.info("  Wrote %d fetch_url rows", len(rows))
    return len(rows)


def migrate_calc_expressions(src, tgt, dry_run):
    """Migrate calc → calc_expression, mapping int dataType to string enum."""
    log.info("--- Migrating calc_expression ---")
    rows = src.execute(text("SELECT id, dataType, expr, time, note FROM calc")).fetchall()

    if dry_run:
        log.info("  [DRY RUN] %d rows", len(rows))
        return len(rows)

    batch = []
    skipped = 0
    for r in rows:
        dt_str = DATATYPE_MAP.get(r[1])
        if dt_str is None:
            log.warning("  Unknown dataType %s for calc id %s, skipping", r[1], r[0])
            skipped += 1
            continue
        batch.append({
            "id": r[0], "data_type": dt_str,
            "expression": r[2], "time_expression": r[3], "note": r[4],
        })
    mysql_upsert(tgt, "calc_expression",
                 ["id", "data_type", "expression", "time_expression", "note"], batch)
    tgt.commit()
    log.info("  Wrote %d calc_expression rows (skipped %d)", len(batch), skipped)
    return len(batch)


def migrate_gauges(src, tgt, dry_run):
    """Migrate gauge table, renaming camelCase → snake_case."""
    log.info("--- Migrating gauges ---")
    rows = src.execute(text(
        "SELECT id, name, bankFull, floodStage, location, latitude, longitude, "
        "stationID, cbttID, geosID, nwsID, nwsliID, snotelID, usgsID, rating "
        "FROM gauge"
    )).fetchall()

    if dry_run:
        log.info("  [DRY RUN] %d rows", len(rows))
        return len(rows)

    batch = [{
        "id": r[0], "name": r[1],
        "bank_full": r[2], "flood_stage": r[3],
        "location": r[4], "latitude": r[5], "longitude": r[6],
        "station_id": r[7], "cbtt_id": r[8], "geos_id": r[9],
        "nws_id": r[10], "nwsli_id": r[11], "snotel_id": r[12],
        "usgs_id": r[13],
        "rating_id": r[14] if r[14] else None,
    } for r in rows]
    mysql_upsert(tgt, "gauge",
                 ["id", "name", "bank_full", "flood_stage", "location",
                  "latitude", "longitude", "station_id", "cbtt_id", "geos_id",
                  "nws_id", "nwsli_id", "snotel_id", "usgs_id", "rating_id"], batch)
    tgt.commit()
    log.info("  Wrote %d gauges", len(rows))
    return len(rows)


def migrate_sources(src, tgt, dry_run):
    """Migrate source table, mapping url/calc int FKs."""
    log.info("--- Migrating sources ---")
    rows = src.execute(text("SELECT id, url, calc, name, agency FROM source")).fetchall()

    if dry_run:
        log.info("  [DRY RUN] %d rows", len(rows))
        return len(rows)

    batch = [{
        "id": r[0], "name": r[3], "agency": r[4],
        "fetch_url_id": r[1] if r[1] and r[1] != 0 else None,
        "calc_expression_id": r[2] if r[2] and r[2] != 0 else None,
    } for r in rows]
    mysql_upsert(tgt, "source",
                 ["id", "name", "agency", "fetch_url_id", "calc_expression_id"], batch)
    tgt.commit()
    log.info("  Wrote %d sources", len(rows))
    return len(rows)


def migrate_gauge_source(src, tgt, dry_run):
    """Migrate gauge2source → gauge_source."""
    log.info("--- Migrating gauge_source ---")
    rows = src.execute(text("SELECT gauge, src FROM gauge2source")).fetchall()

    if dry_run:
        log.info("  [DRY RUN] %d rows", len(rows))
        return len(rows)

    batch = [{"gauge_id": r[0], "source_id": r[1]} for r in rows]
    mysql_upsert(tgt, "gauge_source", ["gauge_id", "source_id"], batch)
    tgt.commit()
    log.info("  Wrote %d gauge_source rows", len(rows))
    return len(rows)


def migrate_sections(src, tgt, dry_run):
    """Migrate section table, renaming camelCase → snake_case."""
    log.info("--- Migrating sections ---")
    rows = src.execute(text(
        "SELECT id, tUpdate, gauge, name, displayName, sortName, nature, "
        "description, difficulties, basin, basinArea, elevation, elevationLost, "
        "length, gradient, features, latitude, longitude, "
        "latitudeStart, longitudeStart, latitudeEnd, longitudeEnd, "
        "mapName, noShow, notes, optimalFlow, region, remoteness, scenery, "
        "season, watershedType, awID "
        "FROM section"
    )).fetchall()

    if dry_run:
        log.info("  [DRY RUN] %d rows", len(rows))
        return len(rows)

    cols = ["id", "updated_at", "gauge_id", "name", "display_name", "sort_name",
            "nature", "description", "difficulties", "basin", "basin_area",
            "elevation", "elevation_lost", "length", "gradient", "features",
            "latitude", "longitude", "latitude_start", "longitude_start",
            "latitude_end", "longitude_end", "map_name", "no_show", "notes",
            "optimal_flow", "region", "remoteness", "scenery", "season",
            "watershed_type", "aw_id"]
    batch = []
    for r in rows:
        gauge_id = r[2] if r[2] and r[2] != 0 else None
        batch.append({
            "id": r[0], "updated_at": r[1], "gauge_id": gauge_id,
            "name": r[3], "display_name": r[4], "sort_name": r[5],
            "nature": r[6], "description": r[7], "difficulties": r[8],
            "basin": r[9], "basin_area": r[10], "elevation": r[11],
            "elevation_lost": r[12], "length": r[13], "gradient": r[14],
            "features": r[15], "latitude": r[16], "longitude": r[17],
            "latitude_start": r[18], "longitude_start": r[19],
            "latitude_end": r[20], "longitude_end": r[21],
            "map_name": r[22], "no_show": 1 if r[23] else 0,
            "notes": r[24], "optimal_flow": r[25],
            "region": r[26], "remoteness": r[27], "scenery": r[28],
            "season": r[29],
            "watershed_type": r[30] if r[30] is not None else None,
            "aw_id": r[31] if r[31] is not None else None,
        })
    mysql_upsert(tgt, "section", cols, batch)
    tgt.commit()
    log.info("  Wrote %d sections", len(rows))
    return len(rows)


def migrate_section_state(src, tgt, dry_run):
    """Migrate section2state → section_state."""
    log.info("--- Migrating section_state ---")
    rows = src.execute(text("SELECT section, state FROM section2state")).fetchall()

    if dry_run:
        log.info("  [DRY RUN] %d rows", len(rows))
        return len(rows)

    batch = [{"section_id": r[0], "state_id": r[1]} for r in rows]
    mysql_upsert(tgt, "section_state", ["section_id", "state_id"], batch)
    tgt.commit()
    log.info("  Wrote %d section_state rows", len(rows))
    return len(rows)


def migrate_section_class(src, tgt, dry_run):
    """Migrate class → section_class, mapping int dataType to string enum."""
    log.info("--- Migrating section_class ---")
    rows = src.execute(
        text("SELECT section, name, low, lowDatatype, high, highDatatype FROM class")
    ).fetchall()

    if dry_run:
        log.info("  [DRY RUN] %d rows", len(rows))
        return len(rows)

    batch = []
    skipped = 0
    for r in rows:
        low_dt = DATATYPE_MAP.get(r[3])
        high_dt = DATATYPE_MAP.get(r[5])
        if low_dt is None or high_dt is None:
            skipped += 1
            continue
        batch.append({
            "section_id": r[0], "name": r[1],
            "low": r[2], "low_data_type": low_dt,
            "high": r[4], "high_data_type": high_dt,
        })
    mysql_upsert(tgt, "section_class",
                 ["section_id", "name", "low", "low_data_type", "high", "high_data_type"], batch)
    tgt.commit()
    log.info("  Wrote %d section_class rows (skipped %d)", len(batch), skipped)
    return len(batch)


def migrate_section_level(src, tgt, dry_run):
    """Migrate section2level → section_level, mapping int enums to strings."""
    log.info("--- Migrating section_level ---")
    try:
        rows = src.execute(
            text("SELECT section, level, low, lowDatatype, high, highDatatype FROM section2level")
        ).fetchall()
    except Exception:
        rows = src.execute(
            text("SELECT section, level, low, lowDatatype, high, highDatatype FROM section2levels")
        ).fetchall()

    if dry_run:
        log.info("  [DRY RUN] %d rows", len(rows))
        return len(rows)

    batch = []
    skipped = 0
    for r in rows:
        level_str = LEVEL_MAP.get(r[1])
        low_dt = DATATYPE_MAP.get(r[3])
        high_dt = DATATYPE_MAP.get(r[5])
        if level_str is None or low_dt is None or high_dt is None:
            skipped += 1
            continue
        batch.append({
            "section_id": r[0], "level": level_str,
            "low": r[2], "low_data_type": low_dt,
            "high": r[4], "high_data_type": high_dt,
        })
    mysql_upsert(tgt, "section_level",
                 ["section_id", "level", "low", "low_data_type", "high", "high_data_type"], batch)
    tgt.commit()
    log.info("  Wrote %d section_level rows (skipped %d)", len(batch), skipped)
    return len(batch)


def migrate_section_guidebook(src, tgt, dry_run):
    """Migrate section2GuideBook → section_guidebook."""
    log.info("--- Migrating section_guidebook ---")
    rows = src.execute(
        text("SELECT section, guideBook, page, run, url FROM section2GuideBook")
    ).fetchall()

    if dry_run:
        log.info("  [DRY RUN] %d rows", len(rows))
        return len(rows)

    batch = [{
        "section_id": r[0], "guidebook_id": r[1],
        "page": r[2], "run": r[3], "url": r[4],
    } for r in rows]
    mysql_upsert(tgt, "section_guidebook",
                 ["section_id", "guidebook_id", "page", "run", "url"], batch)
    tgt.commit()
    log.info("  Wrote %d section_guidebook rows", len(rows))
    return len(rows)


# ---------------------------------------------------------------------------
# Observation migration (levels_data → wkcclevels)
# ---------------------------------------------------------------------------

def get_legacy_observation_tables(legacy_data_engine):
    """Return list of (prefix, source_name, table_name) from legacy data DB."""
    with legacy_data_engine.connect() as conn:
        rows = conn.execute(text("SHOW TABLES")).fetchall()
        table_names = [r[0] for r in rows]

    results = []
    for tname in table_names:
        for prefix in LEGACY_PREFIXES:
            if tname.startswith(f"{prefix}_"):
                source_name = tname[len(prefix) + 1:]
                results.append((prefix, source_name, tname))
                break
    return results


def build_source_map(target_session):
    """Build a dict mapping source.name → source.id from the target DB."""
    sources = target_session.execute(
        text("SELECT id, name FROM source")
    ).fetchall()
    return {name: sid for sid, name in sources}


def migrate_observation_table(
    legacy_data_engine, target_session, table_name,
    source_id, data_type, since, dry_run, batch_size,
):
    """Sync one legacy per-station table into the target observation table."""
    query = f"SELECT time, value FROM `{table_name}`"
    params = {}
    if since:
        query += " WHERE time >= :since"
        params["since"] = since

    with legacy_data_engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    if not rows:
        return 0

    if dry_run:
        log.debug("  [DRY RUN] Would sync %d rows from %s", len(rows), table_name)
        return len(rows)

    count = 0
    batch = []
    for row_time, value in rows:
        if value is None or row_time is None:
            continue
        if isinstance(row_time, str):
            try:
                row_time = datetime.fromisoformat(row_time)
            except ValueError:
                continue

        batch.append({
            "source_id": source_id,
            "observed_at": row_time,
            "data_type": data_type,
            "value": float(value),
        })
        count += 1

        if len(batch) >= batch_size:
            target_session.execute(
                text(
                    "INSERT INTO observation (source_id, observed_at, data_type, value) "
                    "VALUES (:source_id, :observed_at, :data_type, :value) "
                    "ON DUPLICATE KEY UPDATE value = VALUES(value)"
                ),
                batch,
            )
            target_session.flush()
            batch = []

    if batch:
        target_session.execute(
            text(
                "INSERT INTO observation (source_id, observed_at, data_type, value) "
                "VALUES (:source_id, :observed_at, :data_type, :value) "
                "ON DUPLICATE KEY UPDATE value = VALUES(value)"
            ),
            batch,
        )
        target_session.flush()

    return count


def migrate_observations(legacy_data_engine, target_session, since, dry_run, batch_size):
    """Migrate all per-station observation tables from levels_data."""
    log.info("--- Migrating observations ---")

    tables = get_legacy_observation_tables(legacy_data_engine)
    log.info("  Found %d legacy observation tables", len(tables))

    source_map = build_source_map(target_session)
    log.info("  Found %d sources in target DB", len(source_map))

    total = 0
    skipped = 0
    migrated_tables = 0
    start = time.time()

    for prefix, source_name, table_name in tables:
        source_id = source_map.get(source_name)
        if source_id is None:
            log.debug("  No source mapping for %s — skipping %s", source_name, table_name)
            skipped += 1
            continue

        data_type = LEGACY_PREFIXES[prefix]
        count = migrate_observation_table(
            legacy_data_engine, target_session, table_name,
            source_id, data_type, since, dry_run, batch_size,
        )
        if count > 0:
            migrated_tables += 1
            log.debug("  %s → source_id=%d (%s): %d rows",
                      table_name, source_id, data_type, count)
        total += count

        # Periodic commit
        if not dry_run and migrated_tables % 100 == 0 and migrated_tables > 0:
            target_session.commit()
            elapsed = time.time() - start
            log.info("  Progress: %d tables, %d rows (%.0fs)...",
                     migrated_tables, total, elapsed)

    if not dry_run:
        target_session.commit()

    elapsed = time.time() - start
    log.info("  %s %d rows from %d tables (%d skipped) in %.0fs",
             "[DRY RUN]" if dry_run else "Wrote", total, migrated_tables, skipped, elapsed)
    return total


def migrate_latest(legacy_data_engine, target_session, dry_run):
    """Migrate the Latest table → latest_observation."""
    log.info("--- Migrating latest_observation ---")

    source_map = build_source_map(target_session)

    with legacy_data_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name, time, value, prevTime, prevValue, delta FROM Latest")
        ).fetchall()

    if not rows:
        log.info("  No Latest rows found")
        return 0

    count = 0
    for name, obs_time, value, prev_time, prev_value, delta in rows:
        if not name or value is None:
            continue
        parts = name.split("_", 1)
        if len(parts) != 2:
            continue
        prefix, source_name = parts
        if prefix not in LEGACY_PREFIXES:
            continue
        source_id = source_map.get(source_name)
        if source_id is None:
            continue

        data_type = LEGACY_PREFIXES[prefix]

        if dry_run:
            count += 1
            continue

        target_session.execute(
            text(
                "INSERT INTO latest_observation "
                "(source_id, data_type, observed_at, value, "
                " prev_observed_at, prev_value, delta_per_hour) "
                "VALUES (:source_id, :data_type, :observed_at, :value, "
                " :prev_observed_at, :prev_value, :delta_per_hour) "
                "ON DUPLICATE KEY UPDATE "
                " observed_at = VALUES(observed_at), "
                " value = VALUES(value), "
                " prev_observed_at = VALUES(prev_observed_at), "
                " prev_value = VALUES(prev_value), "
                " delta_per_hour = VALUES(delta_per_hour)"
            ),
            {
                "source_id": source_id,
                "data_type": data_type,
                "observed_at": obs_time,
                "value": float(value),
                "prev_observed_at": prev_time,
                "prev_value": float(prev_value) if prev_value is not None else None,
                "delta_per_hour": float(delta) if delta is not None else None,
            },
        )
        count += 1

    if not dry_run:
        target_session.commit()

    log.info("  %s %d latest_observation rows",
             "[DRY RUN]" if dry_run else "Wrote", count)
    return count


# ---------------------------------------------------------------------------
# Page migration (levels_page → wkcclevels)
# ---------------------------------------------------------------------------

def migrate_pages(legacy_page_engine, target_session, dry_run):
    """Migrate Pages table from levels_page → pages."""
    log.info("--- Migrating pages ---")

    with legacy_page_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name, action, expires, modified, mimetype, page FROM Pages")
        ).fetchall()

    if not rows:
        log.info("  No page rows found")
        return 0

    if dry_run:
        log.info("  [DRY RUN] %d rows", len(rows))
        return len(rows)

    batch = [{
        "name": r[0], "action": r[1], "expires": r[2],
        "modified": r[3], "mimetype": r[4], "body": r[5],
    } for r in rows]
    mysql_upsert(
        target_session, "pages",
        ["name", "action", "expires", "modified", "mimetype", "body"],
        batch,
        update_cols=["action", "expires", "modified", "mimetype", "body"],
    )
    target_session.commit()
    log.info("  Wrote %d page rows", len(rows))
    return len(rows)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary(target_session):
    """Print row counts for all tables in the target database."""
    log.info("=== Migration Summary ===")
    for table_name in [
        "state", "class_description", "guidebook", "rating", "rating_data",
        "gauge", "fetch_url", "calc_expression", "source", "gauge_source",
        "section", "section_state", "section_class", "section_level",
        "section_guidebook", "observation", "latest_observation", "pages",
    ]:
        try:
            count = target_session.execute(
                text(f"SELECT COUNT(*) FROM `{table_name}`")
            ).scalar()
            log.info("  %-25s %8d rows", table_name, count)
        except Exception:
            log.info("  %-25s   (missing)", table_name)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Migrate legacy MySQL databases to wkcclevels with new normalized schema"
    )
    parser.add_argument(
        "--legacy-host", default=DEFAULT_HOST,
        help=f"MySQL host for all databases (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--legacy-port", type=int, default=DEFAULT_PORT,
        help=f"MySQL port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--legacy-user", default=DEFAULT_USER,
        help=f"MySQL user (default: {DEFAULT_USER})",
    )
    parser.add_argument(
        "--legacy-pass", default=DEFAULT_PASS,
        help="MySQL password",
    )
    parser.add_argument(
        "--legacy-todo", default="levels_todo",
        help="Legacy database with gauge/source/section tables (default: levels_todo)",
    )
    parser.add_argument(
        "--legacy-data", default="levels_data",
        help="Legacy database with per-station observation tables (default: levels_data)",
    )
    parser.add_argument(
        "--legacy-page", default="levels_page",
        help="Legacy database with Pages table (default: levels_page)",
    )
    parser.add_argument(
        "--target", default="wkcclevels",
        help="Target database name (default: wkcclevels)",
    )
    parser.add_argument(
        "--days", type=int, default=None,
        help="Only sync observations from the last N days (default: all)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=5000,
        help="Rows per upsert batch for observations (default: 5000)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be migrated without writing",
    )
    parser.add_argument(
        "--skip-observations", action="store_true",
        help="Skip the large observation data migration",
    )
    parser.add_argument(
        "--skip-pages", action="store_true",
        help="Skip the page cache migration",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    since = None
    if args.days:
        since = datetime.now(timezone.utc) - timedelta(days=args.days)
        since = since.replace(tzinfo=None)  # Strip tzinfo for MySQL
        log.info("Syncing observations since %s", since)

    # Build connection URLs
    h, p, u, pw = args.legacy_host, args.legacy_port, args.legacy_user, args.legacy_pass
    todo_url = make_url(h, p, u, pw, args.legacy_todo)
    data_url = make_url(h, p, u, pw, args.legacy_data)
    page_url = make_url(h, p, u, pw, args.legacy_page)
    target_url = make_url(h, p, u, pw, args.target)

    log.info("=" * 60)
    log.info("Legacy MySQL Migration to New Schema")
    log.info("=" * 60)
    log.info("  Source:  %s@%s:%d/%s", u, h, p, args.legacy_todo)
    log.info("  Data:   %s@%s:%d/%s", u, h, p, args.legacy_data)
    log.info("  Pages:  %s@%s:%d/%s", u, h, p, args.legacy_page)
    log.info("  Target: %s@%s:%d/%s", u, h, p, args.target)
    if args.dry_run:
        log.info("  *** DRY RUN — no changes will be written ***")
    log.info("=" * 60)

    overall_start = time.time()

    todo_engine = create_engine(todo_url)
    target_engine = create_engine(target_url)

    # --- Step 1: Recreate target schema ---
    log.info("")
    log.info("=== Step 1: Recreate target schema ===")
    if not args.dry_run:
        Base.metadata.drop_all(target_engine)
        log.info("  Dropped existing tables")
        Base.metadata.create_all(target_engine)
        log.info("  Created %d tables with new schema", len(Base.metadata.tables))
    else:
        inspector = inspect(target_engine)
        existing = inspector.get_table_names()
        log.info("  [DRY RUN] Would drop %d existing tables", len(existing))
        log.info("  [DRY RUN] Would create %d new tables", len(Base.metadata.tables))

    # --- Step 2: Migrate metadata from levels_todo ---
    log.info("")
    log.info("=== Step 2: Migrate metadata from levels_todo ===")

    src = Session(bind=todo_engine)
    tgt = Session(bind=target_engine)
    try:
        # Reference data (no FK dependencies)
        migrate_states(src, tgt, args.dry_run)
        migrate_class_descriptions(src, tgt, args.dry_run)
        migrate_guidebooks(src, tgt, args.dry_run)

        # Rating/URL/calc (referenced by gauge and source)
        migrate_ratings(src, tgt, args.dry_run)
        migrate_rating_data(src, tgt, args.dry_run)
        migrate_fetch_urls(src, tgt, args.dry_run)
        migrate_calc_expressions(src, tgt, args.dry_run)

        # Gauge and source (depend on rating, fetch_url, calc_expression)
        migrate_gauges(src, tgt, args.dry_run)
        migrate_sources(src, tgt, args.dry_run)
        migrate_gauge_source(src, tgt, args.dry_run)

        # Sections and junctions (depend on gauge, state, guidebook)
        migrate_sections(src, tgt, args.dry_run)
        migrate_section_state(src, tgt, args.dry_run)
        migrate_section_class(src, tgt, args.dry_run)
        migrate_section_level(src, tgt, args.dry_run)
        migrate_section_guidebook(src, tgt, args.dry_run)

        # --- Step 3: Observations from levels_data ---
        if not args.skip_observations:
            log.info("")
            log.info("=== Step 3: Migrate observations from levels_data ===")
            data_engine = create_engine(data_url)
            try:
                migrate_observations(data_engine, tgt, since, args.dry_run, args.batch_size)
                migrate_latest(data_engine, tgt, args.dry_run)
            finally:
                data_engine.dispose()
        else:
            log.info("")
            log.info("=== Step 3: Skipped (--skip-observations) ===")

        # --- Step 4: Pages from levels_page ---
        if not args.skip_pages:
            log.info("")
            log.info("=== Step 4: Migrate pages from levels_page ===")
            page_engine = create_engine(page_url)
            try:
                migrate_pages(page_engine, tgt, args.dry_run)
            finally:
                page_engine.dispose()
        else:
            log.info("")
            log.info("=== Step 4: Skipped (--skip-pages) ===")

        # --- Summary ---
        log.info("")
        if not args.dry_run:
            print_summary(tgt)

    except Exception:
        tgt.rollback()
        raise
    finally:
        src.close()
        tgt.close()
        todo_engine.dispose()
        target_engine.dispose()

    elapsed = time.time() - overall_start
    log.info("")
    log.info("=== Done in %.0fs ===", elapsed)
    if args.dry_run:
        log.info("  *** DRY RUN — no changes were written ***")


if __name__ == "__main__":
    main()
