#!/usr/bin/env python3
"""
Incremental observation sync from legacy MySQL (levels_data) to wkcclevels.

After the initial full migration (migrate_legacy_to_wkcclevels.py), the legacy
C++ system continues writing new observations to levels_data. This script
incrementally pulls only new observations into wkcclevels.observation and
wkcclevels.latest_observation, suitable for cron (e.g., every 15 minutes).

Efficiency: queries the most recent observed_at per (source_id, data_type)
from wkcclevels.observation as a high-water mark, then fetches only rows
newer than that from each legacy table.

Usage:
    # Incremental sync (high-water mark, default)
    python3 scripts/sync_observations.py

    # Force last 7 days (overrides high-water marks)
    python3 scripts/sync_observations.py --days 7

    # Dry run — show per-table new row counts without writing
    python3 scripts/sync_observations.py --dry-run

    # Skip Latest table sync
    python3 scripts/sync_observations.py --skip-latest

    # Debug logging
    python3 scripts/sync_observations.py -v

    # Via SSH tunnel
    ssh -L 3307:mysql.wkcc.dreamhosters.com:3306 tpw@levels.wkcc.org -N &
    python3 scripts/sync_observations.py \
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
    pass
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Add src/ to path so we can import kayak modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

logging.basicConfig(
    format="%(asctime)s %(levelname)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

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


def build_source_map(session):
    """Build a dict mapping source.name → source.id from the target DB."""
    rows = session.execute(text("SELECT id, name FROM source")).fetchall()
    return {name: sid for sid, name in rows}


def build_high_water_marks(session):
    """Build a dict of (source_id, data_type) → max observed_at from target DB."""
    rows = session.execute(
        text(
            "SELECT source_id, data_type, MAX(observed_at) "
            "FROM observation GROUP BY source_id, data_type"
        )
    ).fetchall()
    return {(r[0], r[1]): r[2] for r in rows}


def get_legacy_observation_tables(legacy_engine):
    """Return list of (prefix, source_name, table_name) from legacy data DB."""
    with legacy_engine.connect() as conn:
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


def sync_observation_table(
    legacy_engine, target_session, table_name,
    source_id, data_type, since, dry_run, batch_size,
):
    """Sync one legacy per-station table incrementally into observation.

    Returns count of rows upserted.
    """
    query = f"SELECT time, value FROM `{table_name}`"
    params = {}
    if since:
        query += " WHERE time > :since"
        params["since"] = since

    with legacy_engine.connect() as conn:
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


def sync_observations(legacy_engine, target_session, since_override, dry_run, batch_size):
    """Sync all per-station observation tables incrementally."""
    log.info("--- Syncing observations ---")

    tables = get_legacy_observation_tables(legacy_engine)
    log.info("  Found %d legacy observation tables", len(tables))

    source_map = build_source_map(target_session)
    log.info("  Found %d sources in target DB", len(source_map))

    # Build high-water marks unless overridden by --days
    if since_override:
        hwm = {}
        log.info("  Using --days override: %s", since_override)
    else:
        hwm = build_high_water_marks(target_session)
        log.info("  Loaded %d high-water marks from target DB", len(hwm))

    total = 0
    skipped = 0
    synced_tables = 0
    start = time.time()

    for prefix, source_name, table_name in tables:
        source_id = source_map.get(source_name)
        if source_id is None:
            log.debug("  No source mapping for %s — skipping %s", source_name, table_name)
            skipped += 1
            continue

        data_type = LEGACY_PREFIXES[prefix]

        # Determine the cutoff: --days override or per-table high-water mark
        since = since_override or hwm.get((source_id, data_type))

        count = sync_observation_table(
            legacy_engine, target_session, table_name,
            source_id, data_type, since, dry_run, batch_size,
        )
        if count > 0:
            synced_tables += 1
            log.debug("  %s → source_id=%d (%s): %d rows",
                      table_name, source_id, data_type, count)
        total += count

        # Periodic commit
        if not dry_run and synced_tables % 100 == 0 and synced_tables > 0:
            target_session.commit()
            elapsed = time.time() - start
            log.info("  Progress: %d tables, %d rows (%.0fs)...",
                     synced_tables, total, elapsed)

    if not dry_run:
        target_session.commit()

    elapsed = time.time() - start
    log.info("  %s %d rows from %d tables (%d skipped) in %.1fs",
             "[DRY RUN]" if dry_run else "Synced", total, synced_tables, skipped, elapsed)
    return total


def sync_latest(legacy_engine, target_session, dry_run):
    """Sync the legacy Latest table → latest_observation (full replace)."""
    log.info("--- Syncing latest_observation ---")

    source_map = build_source_map(target_session)

    with legacy_engine.connect() as conn:
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
             "[DRY RUN]" if dry_run else "Synced", count)
    return count


def main():
    parser = argparse.ArgumentParser(
        description="Incremental observation sync from legacy levels_data to wkcclevels"
    )
    parser.add_argument(
        "--legacy-host", default=DEFAULT_HOST,
        help=f"MySQL host (default: {DEFAULT_HOST})",
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
        "--legacy-data", default="levels_data",
        help="Legacy observation database name (default: levels_data)",
    )
    parser.add_argument(
        "--target", default="wkcclevels",
        help="Target database name (default: wkcclevels)",
    )
    parser.add_argument(
        "--days", type=int, default=None,
        help="Override high-water marks: sync last N days (default: incremental)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=5000,
        help="Rows per upsert batch (default: 5000)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be synced without writing",
    )
    parser.add_argument(
        "--skip-latest", action="store_true",
        help="Skip syncing the Latest table",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    since_override = None
    if args.days:
        since_override = datetime.now(timezone.utc) - timedelta(days=args.days)
        since_override = since_override.replace(tzinfo=None)  # Strip for MySQL
        log.info("Overriding high-water marks: syncing since %s", since_override)

    h, p, u, pw = args.legacy_host, args.legacy_port, args.legacy_user, args.legacy_pass
    data_url = make_url(h, p, u, pw, args.legacy_data)
    target_url = make_url(h, p, u, pw, args.target)

    log.info("Incremental observation sync")
    log.info("  Legacy: %s@%s:%d/%s", u, h, p, args.legacy_data)
    log.info("  Target: %s@%s:%d/%s", u, h, p, args.target)
    if args.dry_run:
        log.info("  *** DRY RUN — no changes will be written ***")

    overall_start = time.time()

    legacy_engine = create_engine(data_url)
    target_engine = create_engine(target_url)
    target_session = Session(bind=target_engine)

    try:
        sync_observations(
            legacy_engine, target_session,
            since_override, args.dry_run, args.batch_size,
        )

        if not args.skip_latest:
            sync_latest(legacy_engine, target_session, args.dry_run)
        else:
            log.info("--- Skipping latest_observation (--skip-latest) ---")

    except Exception:
        target_session.rollback()
        raise
    finally:
        target_session.close()
        legacy_engine.dispose()
        target_engine.dispose()

    elapsed = time.time() - overall_start
    log.info("Done in %.1fs", elapsed)


if __name__ == "__main__":
    main()
