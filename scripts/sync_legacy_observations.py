#!/usr/bin/env python3
"""
Sync observation data from the legacy MySQL levels_data database into the
new schema (SQLite or MySQL).

The legacy database stores observations in per-station tables:
    flow_{source_name}      (time, value)
    gage_{source_name}      (time, value)
    temperature_{source_name} (time, value)

This script reads those tables and inserts/upserts into the new unified
`observation` table keyed by (source_id, observed_at, data_type).

Credentials are read from ~/.config/wkcc/legacy_db.json:
    {
        "host": "mysql.wkcc.dreamhosters.com",
        "user": "levels",
        "password": "...",
        "database": "levels_data"
    }

Usage:
    # Sync into local SQLite (default)
    python3 scripts/sync_legacy_observations.py

    # Sync into production MySQL target
    python3 scripts/sync_legacy_observations.py \
        --target mysql+pymysql://user:pass@host/wkcc_levels

    # Sync only last 7 days
    python3 scripts/sync_legacy_observations.py --days 7

    # Dry run — show what would be synced
    python3 scripts/sync_legacy_observations.py --dry-run

    # Sync via SSH tunnel (run from local machine)
    ssh -L 3307:mysql.wkcc.dreamhosters.com:3306 tpw@levels.wkcc.org -N &
    python3 scripts/sync_legacy_observations.py \
        --legacy mysql+pymysql://user:pass@127.0.0.1:3307/levels_data
"""

import argparse
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pymysql  # noqa: F401 — needed for mysql+pymysql:// URLs
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

# Add src/ to path so we can import kayak modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kayak.db.models import Base, DataType, Observation, Source

logging.basicConfig(
    format="%(asctime)s %(levelname)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# Legacy table prefix → new DataType
LEGACY_PREFIXES = {
    "flow": DataType.flow,
    "gage": DataType.gauge,
    "temperature": DataType.temperature,
}

CONFIG_PATH = Path.home() / ".config" / "wkcc" / "legacy_db.json"
DEFAULT_TARGET_SQLITE = f"sqlite:///{(Path(__file__).parent.parent / '../DB/kayak.db').resolve()}"


def load_legacy_url():
    """Build the legacy MySQL URL from ~/.config/wkcc/legacy_db.json."""
    if not CONFIG_PATH.exists():
        sys.exit(f"Legacy DB config not found: {CONFIG_PATH}")
    cfg = json.loads(CONFIG_PATH.read_text())
    user = cfg["user"]
    password = cfg["password"]
    host = cfg.get("host", "localhost")
    port = cfg.get("port", 3306)
    database = cfg.get("database", "levels_data")
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"


def make_sqlite_engine(url):
    """Create a SQLite engine with WAL and foreign keys."""
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def get_legacy_tables(legacy_engine):
    """Return list of (prefix, source_name, table_name) from legacy DB."""
    with legacy_engine.connect() as conn:
        if "mysql" in str(legacy_engine.url):
            rows = conn.execute(text("SHOW TABLES")).fetchall()
            table_names = [r[0] for r in rows]
        else:
            rows = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
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


def sync_table(
    legacy_engine,
    target_session,
    table_name: str,
    source_id: int,
    data_type: DataType,
    since: datetime | None,
    dry_run: bool,
    batch_size: int,
) -> int:
    """Sync one legacy table into the target observation table.

    Returns count of rows upserted.
    """
    query = f"SELECT time, value FROM `{table_name}`"
    params = {}
    if since:
        query += " WHERE time >= :since"
        params["since"] = since

    with legacy_engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    if not rows:
        return 0

    if dry_run:
        log.info("  [DRY RUN] Would sync %d rows from %s", len(rows), table_name)
        return len(rows)

    count = 0
    batch = []
    is_sqlite = str(target_session.bind.url).startswith("sqlite")

    for row_time, value in rows:
        if value is None:
            continue
        if row_time is None:
            continue
        # Ensure datetime
        if isinstance(row_time, str):
            try:
                row_time = datetime.fromisoformat(row_time)
            except ValueError:
                continue

        # Normalize to second precision to avoid .000000 suffix in SQLite
        row_time = row_time.replace(microsecond=0)

        batch.append({
            "source_id": source_id,
            "observed_at": row_time,
            "data_type": data_type.value,
            "value": float(value),
        })
        count += 1

        if len(batch) >= batch_size:
            _upsert_batch(target_session, batch, is_sqlite)
            batch = []

    if batch:
        _upsert_batch(target_session, batch, is_sqlite)

    return count


def _upsert_batch(session, batch, is_sqlite):
    """Insert or update a batch of observation rows."""
    if is_sqlite:
        session.execute(
            text("""
                INSERT INTO observation (source_id, observed_at, data_type, value)
                VALUES (:source_id, :observed_at, :data_type, :value)
                ON CONFLICT(source_id, observed_at, data_type)
                DO UPDATE SET value = excluded.value
            """),
            batch,
        )
    else:
        # MySQL upsert
        session.execute(
            text("""
                INSERT INTO observation (source_id, observed_at, data_type, value)
                VALUES (:source_id, :observed_at, :data_type, :value)
                ON DUPLICATE KEY UPDATE value = VALUES(value)
            """),
            batch,
        )
    session.flush()


def sync_latest(
    legacy_engine,
    target_session,
    source_map: dict[str, int],
    dry_run: bool,
) -> int:
    """Sync the legacy Latest table into latest_observation."""
    with legacy_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name, time, value, prevTime, prevValue, delta FROM Latest")
        ).fetchall()

    if not rows:
        return 0

    is_sqlite = str(target_session.bind.url).startswith("sqlite")
    count = 0

    for name, obs_time, value, prev_time, prev_value, delta in rows:
        if not name or value is None:
            continue
        # Parse the name: "flow_14306500" → ("flow", "14306500")
        parts = name.split("_", 1)
        if len(parts) != 2:
            continue
        prefix, source_name = parts
        if prefix not in LEGACY_PREFIXES:
            continue
        source_id = source_map.get(source_name)
        if source_id is None:
            continue

        data_type = LEGACY_PREFIXES[prefix].value
        if dry_run:
            count += 1
            continue

        row = {
            "source_id": source_id,
            "data_type": data_type,
            "observed_at": obs_time,
            "value": float(value),
            "prev_observed_at": prev_time,
            "prev_value": float(prev_value) if prev_value is not None else None,
            "delta_per_hour": float(delta) if delta is not None else None,
        }

        if is_sqlite:
            target_session.execute(
                text("""
                    INSERT INTO latest_observation
                        (source_id, data_type, observed_at, value,
                         prev_observed_at, prev_value, delta_per_hour)
                    VALUES (:source_id, :data_type, :observed_at, :value,
                            :prev_observed_at, :prev_value, :delta_per_hour)
                    ON CONFLICT(source_id, data_type)
                    DO UPDATE SET
                        observed_at = excluded.observed_at,
                        value = excluded.value,
                        prev_observed_at = excluded.prev_observed_at,
                        prev_value = excluded.prev_value,
                        delta_per_hour = excluded.delta_per_hour
                """),
                row,
            )
        else:
            target_session.execute(
                text("""
                    INSERT INTO latest_observation
                        (source_id, data_type, observed_at, value,
                         prev_observed_at, prev_value, delta_per_hour)
                    VALUES (:source_id, :data_type, :observed_at, :value,
                            :prev_observed_at, :prev_value, :delta_per_hour)
                    ON DUPLICATE KEY UPDATE
                        observed_at = VALUES(observed_at),
                        value = VALUES(value),
                        prev_observed_at = VALUES(prev_observed_at),
                        prev_value = VALUES(prev_value),
                        delta_per_hour = VALUES(delta_per_hour)
                """),
                row,
            )
        count += 1

    if not dry_run:
        target_session.flush()

    if dry_run:
        log.info("[DRY RUN] Would sync %d latest_observation rows", count)

    return count


def main():
    parser = argparse.ArgumentParser(
        description="Sync observations from legacy MySQL levels_data to new schema"
    )
    parser.add_argument(
        "--legacy",
        default=None,
        help="Legacy MySQL connection URL (default: loaded from ~/.config/wkcc/legacy_db.json)",
    )
    parser.add_argument(
        "--target",
        default=DEFAULT_TARGET_SQLITE,
        help="Target database URL — SQLite or MySQL (default: local kayak.db)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Only sync observations from the last N days (default: all)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5000,
        help="Rows per upsert batch (default: 5000)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be synced without writing",
    )
    parser.add_argument(
        "--skip-latest",
        action="store_true",
        help="Skip syncing the Latest table",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    since = None
    if args.days:
        since = datetime.now(UTC) - timedelta(days=args.days)
        # Strip tzinfo for MySQL compatibility
        since = since.replace(tzinfo=None)
        log.info("Syncing observations since %s", since)

    # Connect to legacy DB
    legacy_url = args.legacy or load_legacy_url()
    log.info("Connecting to legacy DB: %s", legacy_url.split("@")[-1])
    legacy_engine = create_engine(legacy_url)

    # Connect to target DB
    log.info("Connecting to target DB: %s", args.target.split("@")[-1])
    if args.target.startswith("sqlite"):
        target_engine = make_sqlite_engine(args.target)
    else:
        target_engine = create_engine(args.target)

    # Ensure target tables exist
    Base.metadata.create_all(target_engine)

    # Get legacy tables
    tables = get_legacy_tables(legacy_engine)
    log.info("Found %d legacy observation tables", len(tables))

    target_session = Session(bind=target_engine)
    try:
        # Build source name → id map from target
        source_map = build_source_map(target_session)
        log.info("Found %d sources in target DB", len(source_map))

        total = 0
        skipped = 0
        for prefix, source_name, table_name in tables:
            source_id = source_map.get(source_name)
            if source_id is None:
                log.debug("No source mapping for %s — skipping %s", source_name, table_name)
                skipped += 1
                continue

            data_type = LEGACY_PREFIXES[prefix]
            count = sync_table(
                legacy_engine, target_session, table_name,
                source_id, data_type, since, args.dry_run, args.batch_size,
            )
            if count > 0:
                log.info("  %s → source_id=%d (%s): %d rows", table_name, source_id, data_type.value, count)
            total += count

        log.info("Synced %d observation rows (%d tables skipped — no source mapping)", total, skipped)

        # Sync Latest table
        if not args.skip_latest:
            latest_count = sync_latest(legacy_engine, target_session, source_map, args.dry_run)
            log.info("Synced %d latest_observation rows", latest_count)

        if not args.dry_run:
            target_session.commit()
            log.info("Committed to target DB")
        else:
            log.info("[DRY RUN] No changes written")

    except Exception:
        target_session.rollback()
        raise
    finally:
        target_session.close()
        legacy_engine.dispose()
        target_engine.dispose()


if __name__ == "__main__":
    main()
