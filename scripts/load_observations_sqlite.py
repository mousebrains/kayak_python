#!/usr/bin/env python3
"""
Load observation and latest_observation CSV dumps into a SQLite database.

Expects CSV files produced by the MySQL dump alongside this script or
specified via arguments. Intended to run on a remote machine that may
not have the full kayak package installed.

Usage:
    python3 scripts/load_observations_sqlite.py

    # Custom paths
    python3 scripts/load_observations_sqlite.py \
        --db kayak.db \
        --observations observation.csv \
        --latest latest_observation.csv

    # Skip latest_observation
    python3 scripts/load_observations_sqlite.py --skip-latest
"""

import argparse
import csv
import logging
import sqlite3
import time
from pathlib import Path

logging.basicConfig(
    format="%(asctime)s %(levelname)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

BATCH_SIZE = 50_000

CREATE_OBSERVATION = """\
CREATE TABLE IF NOT EXISTS observation (
    source_id   INTEGER NOT NULL,
    observed_at DATETIME NOT NULL,
    data_type   VARCHAR(16) NOT NULL CHECK(data_type IN ('gauge','flow','inflow','temperature')),
    value       FLOAT NOT NULL,
    PRIMARY KEY (source_id, observed_at, data_type),
    FOREIGN KEY (source_id) REFERENCES source(id) ON DELETE CASCADE
);
"""

CREATE_LATEST_OBSERVATION = """\
CREATE TABLE IF NOT EXISTS latest_observation (
    source_id        INTEGER NOT NULL,
    data_type        VARCHAR(16) NOT NULL CHECK(data_type IN ('gauge','flow','inflow','temperature')),
    observed_at      DATETIME NOT NULL,
    value            FLOAT NOT NULL,
    prev_observed_at DATETIME,
    prev_value       FLOAT,
    delta_per_hour   FLOAT,
    PRIMARY KEY (source_id, data_type),
    FOREIGN KEY (source_id) REFERENCES source(id) ON DELETE CASCADE
);
"""

UPSERT_OBSERVATION = """\
INSERT INTO observation (source_id, observed_at, data_type, value)
VALUES (?, ?, ?, ?)
ON CONFLICT(source_id, observed_at, data_type)
DO UPDATE SET value = excluded.value;
"""

UPSERT_LATEST = """\
INSERT INTO latest_observation
    (source_id, data_type, observed_at, value,
     prev_observed_at, prev_value, delta_per_hour)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(source_id, data_type)
DO UPDATE SET
    observed_at      = excluded.observed_at,
    value            = excluded.value,
    prev_observed_at = excluded.prev_observed_at,
    prev_value       = excluded.prev_value,
    delta_per_hour   = excluded.delta_per_hour;
"""


def load_observations(conn, csv_path):
    """Load observation.csv into the observation table."""
    log.info("Loading observations from %s", csv_path)
    start = time.time()
    cursor = conn.cursor()

    count = 0
    batch = []
    with open(csv_path, newline="") as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        for row in reader:
            source_id, observed_at, data_type, value = row
            batch.append((int(source_id), observed_at, data_type, float(value)))
            if len(batch) >= BATCH_SIZE:
                cursor.executemany(UPSERT_OBSERVATION, batch)
                count += len(batch)
                batch = []
                if count % 500_000 == 0:
                    log.info("  %d rows...", count)

    if batch:
        cursor.executemany(UPSERT_OBSERVATION, batch)
        count += len(batch)

    conn.commit()
    elapsed = time.time() - start
    log.info("  Loaded %d observation rows in %.1fs", count, elapsed)
    return count


def load_latest(conn, csv_path):
    """Load latest_observation.csv into the latest_observation table."""
    log.info("Loading latest_observation from %s", csv_path)
    cursor = conn.cursor()

    count = 0
    with open(csv_path, newline="") as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        for row in reader:
            source_id, data_type, observed_at, value, prev_at, prev_val, delta = row
            cursor.execute(UPSERT_LATEST, (
                int(source_id),
                data_type,
                observed_at,
                float(value),
                prev_at if prev_at else None,
                float(prev_val) if prev_val else None,
                float(delta) if delta else None,
            ))
            count += 1

    conn.commit()
    log.info("  Loaded %d latest_observation rows", count)
    return count


def main():
    default_db = str((Path(__file__).parent.parent / "../DB/kayak.db").resolve())
    default_obs = str(Path(__file__).parent.parent / "observation.csv")
    default_latest = str(Path(__file__).parent.parent / "latest_observation.csv")

    parser = argparse.ArgumentParser(
        description="Load observation CSV dumps into a SQLite database"
    )
    parser.add_argument(
        "--db", default=default_db,
        help=f"SQLite database path (default: {default_db})",
    )
    parser.add_argument(
        "--observations", default=default_obs,
        help=f"Path to observation.csv (default: {default_obs})",
    )
    parser.add_argument(
        "--latest", default=default_latest,
        help=f"Path to latest_observation.csv (default: {default_latest})",
    )
    parser.add_argument(
        "--skip-latest", action="store_true",
        help="Skip loading the latest_observation table",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    log.info("Database: %s", args.db)

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        conn.execute(CREATE_OBSERVATION)
        conn.execute(CREATE_LATEST_OBSERVATION)

        load_observations(conn, args.observations)

        if not args.skip_latest:
            load_latest(conn, args.latest)
        else:
            log.info("Skipping latest_observation (--skip-latest)")

    finally:
        conn.close()

    log.info("Done.")


if __name__ == "__main__":
    main()
