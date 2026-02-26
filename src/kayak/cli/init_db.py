"""Database initialization command (replaces gen.sql/rebuild)."""

import click

from kayak.config_data import load_sources
from kayak.db.engine import get_engine
from kayak.db.models import Base, FetchUrl, State


def _seed_states(session):
    """Seed state reference data."""
    states = [
        ("UT", "UT"), ("OR", "OR"), ("AZ", "AZ"), ("CA", "CA"),
        ("WA", "WA"), ("CO", "CO"), ("KS", "KS"), ("MT", "MT"),
        ("ID", "ID"), ("WY", "WY"), ("NV", "NV"), ("NM", "NM"),
    ]
    for name, abbr in states:
        existing = session.query(State).filter_by(name=name).first()
        if not existing:
            session.add(State(name=name, abbreviation=abbr))


def _sync_sources(session):
    """Sync URL/parser definitions from data/sources.yaml into FetchUrl table."""
    sources = load_sources()
    count = 0
    for src in sources:
        url = src["url"]
        existing = session.query(FetchUrl).filter_by(url=url).first()
        if existing:
            existing.parser = src["parser"]
            existing.hours = src.get("hours", "")
            existing.is_active = True
        else:
            session.add(FetchUrl(
                url=url,
                parser=src["parser"],
                hours=src.get("hours", ""),
                is_active=True,
            ))
            count += 1
    return count


@click.command("init-db")
@click.option("--drop", is_flag=True, help="Drop and recreate all tables")
@click.option("--seed/--no-seed", default=True, help="Seed reference data")
def init_db(drop, seed):
    """Create database tables and optionally seed reference data."""
    engine = get_engine()

    if drop:
        click.echo("Dropping all tables...")
        Base.metadata.drop_all(engine)

    click.echo("Creating tables...")
    Base.metadata.create_all(engine)

    if seed:
        from kayak.db.engine import get_session
        session = get_session()
        try:
            click.echo("Seeding states...")
            _seed_states(session)
            click.echo("Syncing sources from YAML...")
            count = _sync_sources(session)
            click.echo(f"  {count} new FetchUrl records added")
            session.commit()
            click.echo("Done.")
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
