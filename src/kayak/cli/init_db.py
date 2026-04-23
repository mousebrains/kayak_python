"""Database initialization command (replaces gen.sql/rebuild)."""

import argparse
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from kayak.config_data import load_sources
from kayak.db.engine import get_engine
from kayak.db.models import Base, FetchUrl, Source, State

logger = logging.getLogger(__name__)


def _validate_tz(tz_name: str, context: str) -> None:
    """Fail loud if a YAML-supplied IANA timezone name is unknown."""
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as e:
        raise ValueError(f"Invalid IANA timezone {tz_name!r} for {context}: {e}") from e


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
    """Sync URL/parser definitions from data/sources.yaml into FetchUrl table.

    Upserts every YAML entry with is_active=True, then flips is_active=False
    on any FetchUrl whose URL is NOT in the YAML — so URLs removed from the
    config stop being fetched without deleting the row (observations and
    source rows still reference fetch_url_id via ON DELETE SET NULL).

    Also upserts Source rows for every station listed in a URL's ``stations:``
    block, setting ``source.timezone`` to the IANA TZ name. This pre-creates
    station source rows so the per-station TZ is populated before the first
    parse; parsers still auto-create rows for unknown stations (without a TZ).

    Returns the count of newly-inserted FetchUrl rows. Updates, station
    upserts, and deactivations are logged but not counted in the return value.
    """
    sources = load_sources()
    yaml_urls = {src["url"] for src in sources}
    count = 0
    for src in sources:
        url = src["url"]
        existing = session.execute(select(FetchUrl).where(FetchUrl.url == url)).scalar_one_or_none()
        if existing:
            existing.parser = src["parser"]
            existing.hours = src.get("hours", "")
            existing.is_active = True
            fetch_url_row = existing
        else:
            fetch_url_row = FetchUrl(
                url=url,
                parser=src["parser"],
                hours=src.get("hours", ""),
                is_active=True,
            )
            session.add(fetch_url_row)
            count += 1

        # Flush so new FetchUrl rows get their id before we upsert Source rows.
        session.flush()

        # Upsert Source rows from the stations: block (may be empty).
        stations = src.get("stations") or {}
        for station_name, tz_name in stations.items():
            _validate_tz(tz_name, f"station {station_name!r} on {url}")
            src_row = session.execute(
                select(Source).where(
                    Source.name == station_name,
                    Source.fetch_url_id == fetch_url_row.id,
                )
            ).scalar_one_or_none()
            if src_row:
                if src_row.timezone != tz_name:
                    logger.info(
                        "Updating timezone for source %s (id=%d): %s → %s",
                        station_name,
                        src_row.id,
                        src_row.timezone,
                        tz_name,
                    )
                    src_row.timezone = tz_name
                if src_row.agency is None:
                    src_row.agency = src["parser"]
            else:
                session.add(
                    Source(
                        name=station_name,
                        agency=src["parser"],
                        fetch_url_id=fetch_url_row.id,
                        timezone=tz_name,
                    )
                )
                logger.info(
                    "Created source %s (fetch_url=%d, tz=%s)",
                    station_name,
                    fetch_url_row.id,
                    tz_name,
                )

    # Deactivate rows whose URL has left the YAML. This is how sources get
    # retired — we never DELETE the row because observation/source rows
    # still reference it via fetch_url_id.
    if yaml_urls:
        stale = (
            session.execute(
                select(FetchUrl).where(
                    FetchUrl.url.notin_(yaml_urls),
                    FetchUrl.is_active.is_(True),
                )
            )
            .scalars()
            .all()
        )
        if stale:
            for fu in stale:
                fu.is_active = False
            logger.info("Deactivated %d fetch_url row(s) missing from sources.yaml", len(stale))
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
