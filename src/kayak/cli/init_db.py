"""Database initialization command (replaces gen.sql/rebuild).

Creates the schema with ``Base.metadata.create_all()`` and stamps the packaged
migrations on a truly fresh DB. Schema only — states and all other metadata
arrive through ``levels sync-metadata`` from the dataset (S1-cleanup removed
the former ``sources.yaml`` state/source seeding; the dataset registry +
``levels generate-sources`` own source/fetch_url definitions).
"""

import argparse
import logging

from sqlalchemy import text

from kayak.db.engine import get_engine
from kayak.db.models import Base

logger = logging.getLogger(__name__)


def addArgs(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the 'init-db' subcommand."""
    parser = subparsers.add_parser("init-db", help="Create database tables and stamp migrations")
    parser.set_defaults(func=init_db)
    parser.add_argument("--drop", action="store_true", help="Drop and recreate all tables")


def init_db(args: argparse.Namespace) -> None:
    """Create database tables and stamp migrations (schema only)."""
    engine = get_engine()

    if args.drop:
        print("Dropping all tables...")
        Base.metadata.drop_all(engine)
        # schema_migrations is raw DDL (migrate._ensure_tracking_table), not part
        # of Base.metadata, so drop_all leaves it behind. Drop it too -- otherwise
        # its stale rows make the freshly create_all'd schema look "already
        # tracked", init-db skips stamping, and `levels migrate` then re-runs
        # migrations the new schema already has (review-4 R5.4).
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS schema_migrations"))

    print("Creating tables...")
    Base.metadata.create_all(engine)

    # Stamping rule: only on a TRULY FRESH DB.
    #
    # On a fresh DB, metadata.create_all produced the target schema, so every
    # known migration is effectively already applied and should be stamped
    # to prevent re-run on next `levels migrate`.
    #
    # On an EXISTING DB (e.g. someone re-runs init-db on the live host), the
    # previous behavior blanket-stamped *every* discovered migration — including
    # ones that hadn't actually been applied yet. The next `levels migrate`
    # would then silently skip them. Now we check schema_migrations: if it
    # has any prior rows, this isn't a fresh init and we defer to `migrate`.
    from kayak.cli.migrate import applied_versions, stamp_all_known

    prior = applied_versions()
    if prior:
        print(
            f"DB already tracks {len(prior)} applied migration(s); "
            f"skipping init-db stamp. Run `levels migrate` to apply pending."
        )
    else:
        stamped = stamp_all_known()
        if stamped:
            print(f"Stamped {stamped} migration(s) as applied.")
    print("Done.")
