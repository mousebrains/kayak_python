"""Database initialization command (replaces gen.sql/rebuild)."""

import click

from kayak.db.engine import get_engine
from kayak.db.models import (
    Base,
    BuilderColumn,
    DescriptionField,
    Parameter,
)


def _seed_parameters(session):
    """Seed system parameters (from gen.sql/mkParameters)."""
    params = [
        ("rootDir", ""),
        ("webPageDir", ""),
        ("templateDir", "web.templates"),
        ("mapSourceDir", "maps"),
        ("web.pre.filename", "levels.head"),
        ("web.post.filename", "levels.tail"),
        ("guide.pre.filename", "description.head"),
        ("guide.post.filename", "description.tail"),
        ("gage.html.pre.filename", "gage.html.head"),
        ("gage.html.post.filename", "gage.html.tail"),
        ("displayCGI", "cgi/display"),
        ("files.csv.suffix", ".csv"),
        ("files.text.suffix", ".text"),
        ("files.html.suffix", ".html"),
        ("files.html.pre.filename", "html.head"),
        ("files.html.post.filename", "html.tail"),
    ]
    for ident, value in params:
        session.merge(Parameter(ident=ident, value=value))


def _seed_description_fields(session):
    """Seed description field metadata (from gen.sql/mkDescription)."""
    fields = [
        (0, "hash_value", "noop", "", "", "Must be first"),
        (1, "display_name", "noop", "", "", "Must be second"),
        (100, "class", "text", "Class", "<br />", None),
        (200, "section", "text", "Section", "<br />", None),
        (300, "state", "text", "State(s)", "<br />", None),
        (400, "drainage", "text", "Drainage", "<br />", None),
        (500, "region", "text", "Region", "<br />", None),
        (600, "gauge_location", "text", "Gauge Location", "<br />", None),
        (700, "station_number", "text", "Station #", "<br />", None),
        (701, "usgs_id", "text", "USGS ID #", "<br />", None),
        (702, "nwsli_id", "text", "NWS ID ", "<br />", None),
        (703, "geos_id", "text", "GEOS ID ", "<br />", None),
        (800, "latitude", "text", "Latitude", "", None),
        (900, "longitude", "text", "Longitude", "<br />", None),
        (1000, "guide_book", "text", "Guide Book", "", None),
        (1100, "run_number", "text", "Run #", "", None),
        (1200, "page_number", "text", "Page ", "<br />", None),
        (1300, "season", "text", "Season", "<br />", None),
        (1400, "length", "text", "Length", " miles<br />", None),
        (1500, "gradient", "text", "Gradient", " feet/mile<br />", None),
        (1600, "elevation_lost", "text", "Elevation Lost", "feet<br />", None),
        (1700, "scenery", "text", "Scenery", "<br />", None),
        (1800, "features", "text", "Features", "<br />", None),
        (1900, "remoteness", "text", "Remoteness", "<br />", None),
        (2000, "character", "text", "Character", "<br />", None),
        (2100, "difficulties", "text", "Difficulties", "<br />", None),
        (2200, "watershed_type", "text", "Watershed type", "<br />", None),
        (2300, "low_flow", "text", "Low Flow", " CFS<br />", None),
        (2400, "high_flow", "text", "High Flow", " CFS<br />", None),
        (2500, "optimal_flow", "text", "Optimal Flow", " CFS<br />", None),
        (2600, "bank_full", "text", "Bank Full", " CFS<br />", None),
        (2700, "flood_stage", "text", "Flood Stage", " CFS<br />", None),
        (2800, "merged_dbs", "noop", "", "", None),
        (2801, "db_name", "DB", "View", "<br />", "Must be right after Merged_DBs"),
        (2900, "calc_time", "noop", "", "", None),
        (2901, "calc_expr", "calc", "Calculation", "<br />", "Must be right after Calc_time"),
        (3000, "calc_notes", "ptxt", "Calculation Notes", "<br />", None),
        (3100, "cfs_to_gauge_data", "URL", "Rating table", "<br />", None),
        (3200, "description", "text", "Description", "", None),
        (3300, "notes", "ptxt", "Notes", "", None),
    ]
    for sort_key, col, typ, prefix, suffix, info in fields:
        session.merge(
            DescriptionField(
                sort_key=sort_key,
                column_name=col,
                type=typ,
                prefix=prefix,
                suffix=suffix,
                info=info,
            )
        )


def _seed_builder_columns(session):
    """Seed builder output configuration (from gen.sql/mkBuilder)."""
    cols = [
        (0, "f", "noop", "hash_value", 0, "", ""),
        (1, "f", "noop", "db_name", 0, "", ""),
        (2, "f", "noop", "state", 0, "", ""),
        (3, "f", "noop", "calc_expr", 0, "", ""),
        (4, "f", "noop", "low_flow", 0, "", ""),
        (5, "f", "noop", "high_flow", 0, "", ""),
        (6, "f", "noop", "class_flow", 0, "", ""),
        (100, "hct", "status", "status", 4, "Status", "Status"),
        (200, "fhct", "name", "display_name", 20, "Name", "Name"),
        (300, "fhct", "text", "gauge_location", 10, "Location", "Location"),
        (400, "hct", "date", "time", 14, "Date", "Date"),
        (500, "hct", "flow", "flow", 7, "Flow",
         '<a href="#Units">Flow<br />CFS</a>'),
        (600, "hct", "gage", "gage", 7, "Height",
         '<a href="#Units">Height<br />Feet</a>'),
        (700, "hct", "temp", "temperature", 4, "Temp",
         '<a href="#Units">Temp<br />F</a>'),
        (800, "fhct", "text", "drainage", 10, "Drainage", "Drainage"),
        (900, "fhct", "text", "class", 7, "Class", "Class"),
    ]
    for sort_key, use, typ, field, length, name_text, name_html in cols:
        session.merge(
            BuilderColumn(
                sort_key=sort_key,
                use=use,
                type=typ,
                field=field,
                length=length,
                name_text=name_text,
                name_html=name_html,
            )
        )


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
            click.echo("Seeding parameters...")
            _seed_parameters(session)
            click.echo("Seeding description fields...")
            _seed_description_fields(session)
            click.echo("Seeding builder columns...")
            _seed_builder_columns(session)
            session.commit()
            click.echo("Done.")
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
