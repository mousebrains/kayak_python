"""Decimate (thin) old observations to reduce database size.

Strategy:
- Recent (< recent_days): keep ALL observations
- Medium-term (recent_days to archive_days): thin to 1 per hour
- Long-term (> archive_days): thin to 1 per 6 hours

Within each time bucket, the observation closest to the bucket midpoint is kept.

Deletions are batched by source_id so the database write lock is held only
briefly per batch, allowing concurrent readers and writers.
"""

import argparse
import logging

from sqlalchemy import text

from kayak.db.engine import get_engine, get_session

logger = logging.getLogger(__name__)

# SQL for hourly thinning (medium-term), scoped to one source
_HOURLY_SQL = """
WITH ranked AS (
    SELECT
        source_id, observed_at, data_type,
        ROW_NUMBER() OVER (
            PARTITION BY source_id, data_type, strftime('%Y-%m-%d %H', observed_at)
            ORDER BY ABS(
                CAST(strftime('%M', observed_at) AS INTEGER) - 30
            )
        ) AS rn
    FROM observation
    WHERE source_id = :source_id
      AND observed_at < :medium_cutoff
      AND observed_at >= :archive_cutoff
)
DELETE FROM observation
WHERE (source_id, observed_at, data_type) IN (
    SELECT source_id, observed_at, data_type FROM ranked WHERE rn > 1
)
"""

# SQL for 6-hourly thinning (long-term), scoped to one source
_6HOURLY_SQL = """
WITH ranked AS (
    SELECT
        source_id, observed_at, data_type,
        ROW_NUMBER() OVER (
            PARTITION BY source_id, data_type,
                strftime('%Y-%m-%d', observed_at),
                CAST(strftime('%H', observed_at) AS INTEGER) / 6
            ORDER BY ABS(
                (CAST(strftime('%H', observed_at) AS INTEGER) % 6) * 60
                + CAST(strftime('%M', observed_at) AS INTEGER) - 180
            )
        ) AS rn
    FROM observation
    WHERE source_id = :source_id
      AND observed_at < :archive_cutoff
)
DELETE FROM observation
WHERE (source_id, observed_at, data_type) IN (
    SELECT source_id, observed_at, data_type FROM ranked WHERE rn > 1
)
"""

# Count queries for dry-run / reporting (global, not per-source)
_HOURLY_COUNT_SQL = """
WITH ranked AS (
    SELECT
        source_id, observed_at, data_type,
        ROW_NUMBER() OVER (
            PARTITION BY source_id, data_type, strftime('%Y-%m-%d %H', observed_at)
            ORDER BY ABS(
                CAST(strftime('%M', observed_at) AS INTEGER) - 30
            )
        ) AS rn
    FROM observation
    WHERE observed_at < :medium_cutoff
      AND observed_at >= :archive_cutoff
)
SELECT COUNT(*) FROM ranked WHERE rn > 1
"""

_6HOURLY_COUNT_SQL = """
WITH ranked AS (
    SELECT
        source_id, observed_at, data_type,
        ROW_NUMBER() OVER (
            PARTITION BY source_id, data_type,
                strftime('%Y-%m-%d', observed_at),
                CAST(strftime('%H', observed_at) AS INTEGER) / 6
            ORDER BY ABS(
                (CAST(strftime('%H', observed_at) AS INTEGER) % 6) * 60
                + CAST(strftime('%M', observed_at) AS INTEGER) - 180
            )
        ) AS rn
    FROM observation
    WHERE observed_at < :archive_cutoff
)
SELECT COUNT(*) FROM ranked WHERE rn > 1
"""


def addArgs(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the 'decimate' subcommand."""
    parser = subparsers.add_parser(
        "decimate",
        help="Thin old observations to reduce database size",
    )
    parser.set_defaults(func=decimate)
    parser.add_argument(
        "--recent-days",
        type=int,
        default=90,
        help="Keep all observations within this many days (default: 90)",
    )
    parser.add_argument(
        "--archive-days",
        type=int,
        default=365,
        help="Thin to 6-hourly beyond this many days (default: 365)",
    )
    parser.add_argument(
        "-d",
        "--dry-run",
        action="store_true",
        help="Show counts only, do not delete",
    )
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="Run VACUUM after deletion to reclaim space",
    )


def decimate(args: argparse.Namespace) -> None:
    """Thin old observations."""
    from datetime import UTC, datetime, timedelta

    recent_days = getattr(args, "recent_days", 90)
    archive_days = getattr(args, "archive_days", 365)
    dry_run = getattr(args, "dry_run", False)
    do_vacuum = getattr(args, "vacuum", False)

    now = datetime.now(UTC)
    medium_cutoff = now - timedelta(days=recent_days)
    archive_cutoff = now - timedelta(days=archive_days)

    params = {
        "medium_cutoff": medium_cutoff.strftime("%Y-%m-%d %H:%M:%S"),
        "archive_cutoff": archive_cutoff.strftime("%Y-%m-%d %H:%M:%S"),
    }

    session = get_session()
    try:
        # Count total observations
        total = session.execute(text("SELECT COUNT(*) FROM observation")).scalar()
        print(f"Total observations: {total:,}")

        # Count observations in each time range
        recent_count = session.execute(
            text("SELECT COUNT(*) FROM observation WHERE observed_at >= :medium_cutoff"),
            {"medium_cutoff": params["medium_cutoff"]},
        ).scalar()
        medium_count = session.execute(
            text(
                "SELECT COUNT(*) FROM observation "
                "WHERE observed_at < :medium_cutoff AND observed_at >= :archive_cutoff"
            ),
            params,
        ).scalar()
        archive_count = session.execute(
            text("SELECT COUNT(*) FROM observation WHERE observed_at < :archive_cutoff"),
            {"archive_cutoff": params["archive_cutoff"]},
        ).scalar()

        print(f"  Recent (<{recent_days}d): {recent_count:,} (keep all)")
        print(f"  Medium ({recent_days}-{archive_days}d): {medium_count:,} (thin to hourly)")
        print(f"  Archive (>{archive_days}d): {archive_count:,} (thin to 6-hourly)")

        # Count deletions
        hourly_deletes = session.execute(text(_HOURLY_COUNT_SQL), params).scalar()
        sixhour_deletes = session.execute(
            text(_6HOURLY_COUNT_SQL),
            {"archive_cutoff": params["archive_cutoff"]},
        ).scalar()

        total_deletes = (hourly_deletes or 0) + (sixhour_deletes or 0)
        print(f"\nWould delete: {total_deletes:,} observations")
        print(f"  Hourly thinning: {hourly_deletes:,}")
        print(f"  6-hourly thinning: {sixhour_deletes:,}")

        if dry_run:
            print("\nDry run — no changes made")
            session.rollback()
            return

        if total_deletes == 0:
            print("\nNothing to decimate")
            return

        # Get distinct source_ids that have observations to decimate
        source_ids = [
            row[0]
            for row in session.execute(
                text(
                    "SELECT DISTINCT source_id FROM observation WHERE observed_at < :medium_cutoff"
                ),
                {"medium_cutoff": params["medium_cutoff"]},
            ).fetchall()
        ]

        # Execute deletions per source, committing after each
        print(f"\nDecimating across {len(source_ids)} sources...")
        total_hourly = 0
        total_6hourly = 0

        for source_id in source_ids:
            src_params = {**params, "source_id": source_id}

            result1 = session.execute(text(_HOURLY_SQL), src_params)
            hourly = result1.rowcount  # type: ignore[attr-defined]

            result2 = session.execute(
                text(_6HOURLY_SQL),
                {"archive_cutoff": params["archive_cutoff"], "source_id": source_id},
            )
            sixhourly = result2.rowcount  # type: ignore[attr-defined]

            if hourly > 0 or sixhourly > 0:
                session.commit()
                total_hourly += hourly
                total_6hourly += sixhourly
                logger.info("source_id=%d: hourly=%d, 6-hourly=%d", source_id, hourly, sixhourly)

        print(f"  Hourly: {total_hourly:,} rows deleted")
        print(f"  6-hourly: {total_6hourly:,} rows deleted")
        print(f"Done — {total_hourly + total_6hourly:,} total rows deleted")

    finally:
        session.close()

    # PRAGMA optimize and optional VACUUM run outside session
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("PRAGMA optimize"))
            if do_vacuum:
                print("Running VACUUM...")
                conn.execute(text("VACUUM"))
            conn.commit()
    except Exception as e:
        logger.warning("Post-decimate maintenance failed: %s", e)

    if do_vacuum:
        print("VACUUM complete")
