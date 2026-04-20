"""Database initialization command (replaces gen.sql/rebuild)."""

import argparse
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from kayak.config_data import load_sources
from kayak.db.engine import get_engine
from kayak.db.models import Base, FetchUrl, State

logger = logging.getLogger(__name__)


def _seed_states(session: Session) -> None:
    """Seed state reference data."""
    states = [
        ("Utah", "UT"),
        ("Oregon", "OR"),
        ("Arizona", "AZ"),
        ("California", "CA"),
        ("Washington", "WA"),
        ("Colorado", "CO"),
        ("Kansas", "KS"),
        ("Montana", "MT"),
        ("Idaho", "ID"),
        ("Wyoming", "WY"),
        ("Nevada", "NV"),
        ("New Mexico", "NM"),
    ]
    for name, abbr in states:
        existing = session.execute(
            select(State).where(State.abbreviation == abbr)
        ).scalar_one_or_none()
        if not existing:
            session.add(State(name=name, abbreviation=abbr))


def sync_sources(session: Session) -> int:
    """Sync URL/parser definitions from data/sources.yaml into FetchUrl table."""
    sources = load_sources()
    count = 0
    for src in sources:
        url = src["url"]
        existing = session.execute(select(FetchUrl).where(FetchUrl.url == url)).scalar_one_or_none()
        if existing:
            existing.parser = src["parser"]
            existing.hours = src.get("hours", "")
            existing.is_active = True
        else:
            session.add(
                FetchUrl(
                    url=url,
                    parser=src["parser"],
                    hours=src.get("hours", ""),
                    is_active=True,
                )
            )
            count += 1
    return count


def addArgs(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the 'init-db' subcommand."""
    parser = subparsers.add_parser(
        "init-db", help="Create database tables and optionally seed reference data"
    )
    parser.set_defaults(func=init_db)
    parser.add_argument("--drop", action="store_true", help="Drop and recreate all tables")
    parser.add_argument("--no-seed", action="store_true", help="Skip seeding reference data")


def init_db(args: argparse.Namespace) -> None:
    """Create database tables and optionally seed reference data."""
    engine = get_engine()

    if args.drop:
        print("Dropping all tables...")
        Base.metadata.drop_all(engine)

    print("Creating tables...")
    Base.metadata.create_all(engine)

    # Fresh DBs start at the head of the migration series — metadata.create_all
    # already produced the target schema, so every known migration is
    # effectively already applied. Stamping them prevents re-run on next
    # `levels migrate`.
    from kayak.cli.migrate import stamp_all_known

    stamped = stamp_all_known()
    if stamped:
        print(f"Stamped {stamped} migration(s) as applied.")

    if not args.no_seed:
        from kayak.db.engine import get_session

        session = get_session()
        try:
            print("Seeding states...")
            _seed_states(session)
            print("Syncing sources from YAML...")
            count = sync_sources(session)
            print(f"  {count} new FetchUrl records added")
            session.commit()
            print("Done.")
        except Exception:
            logger.exception("Error during database initialization")
            session.rollback()
            raise
        finally:
            session.close()
