"""SQLAlchemy ORM models.

Consolidates the three C++ databases (levels_information, levels_data, levels_page)
into a single schema suitable for both SQLite and MySQL.

Key design change: Instead of dynamically creating per-station tables
(flow_X, gage_X, etc.), we use a single ``measurements`` table with a
composite index on (station, data_type, time).
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DataType(str, enum.Enum):
    """Measurement types (replaces DataDB::TYPE)."""
    FLOW = "flow"
    INFLOW = "inflow"
    OUTFLOW = "outflow"
    GAGE = "gage"
    TEMPERATURE = "temperature"


class PageAction(str, enum.Enum):
    """Page cache action types (replaces PageDB::ACTION)."""
    PAGE = "page"
    FILE = "file"
    PLOT = "plot"
    VIEW = "view"
    EDIT = "edit"
    SVG = "svg"
    PNG = "png"


# ---------------------------------------------------------------------------
# levels_information tables
# ---------------------------------------------------------------------------

class Master(Base):
    """River/section master metadata (replaces Master table in levels_information)."""
    __tablename__ = "master"

    hash_value: Mapped[str] = mapped_column(String(8), primary_key=True)
    approved: Mapped[str | None] = mapped_column(Text)
    random_key: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    gauge_location: Mapped[str | None] = mapped_column(Text)
    sort_key: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(Text)
    section: Mapped[str | None] = mapped_column(Text)
    drainage: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(Text)
    river_name: Mapped[str | None] = mapped_column(Text)
    river_class: Mapped[str | None] = mapped_column("class", Text)
    class_flow: Mapped[str | None] = mapped_column(Text)
    length: Mapped[str | None] = mapped_column(Text)
    gradient: Mapped[str | None] = mapped_column(Text)
    elevation_lost: Mapped[str | None] = mapped_column(Text)
    elevation: Mapped[str | None] = mapped_column(Text)
    season: Mapped[str | None] = mapped_column(Text)
    scenery: Mapped[str | None] = mapped_column(Text)
    features: Mapped[str | None] = mapped_column(Text)
    remoteness: Mapped[str | None] = mapped_column(Text)
    character: Mapped[str | None] = mapped_column("nature", Text)
    difficulties: Mapped[str | None] = mapped_column(Text)
    watershed_type: Mapped[str | None] = mapped_column(Text)
    low_flow: Mapped[str | None] = mapped_column(Text)
    high_flow: Mapped[str | None] = mapped_column(Text)
    optimal_flow: Mapped[str | None] = mapped_column(Text)
    bank_full: Mapped[str | None] = mapped_column(Text)
    flood_stage: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[str | None] = mapped_column(Text)
    longitude: Mapped[str | None] = mapped_column(Text)
    guide_book: Mapped[str | None] = mapped_column(Text)
    run_number: Mapped[str | None] = mapped_column("runnumber", Text)
    page_number: Mapped[str | None] = mapped_column("pagenumber", Text)
    station_number: Mapped[str | None] = mapped_column("stationnumber", Text)
    usgs_id: Mapped[str | None] = mapped_column(Text)
    nwsli_id: Mapped[str | None] = mapped_column(Text)
    cbtt_id: Mapped[str | None] = mapped_column(Text)
    nws_id: Mapped[str | None] = mapped_column(Text)
    geos_id: Mapped[str | None] = mapped_column(Text)
    snotel_id: Mapped[str | None] = mapped_column(Text)
    db_name: Mapped[str | None] = mapped_column(Text)
    merged_dbs: Mapped[str | None] = mapped_column(Text)
    calc_type: Mapped[str | None] = mapped_column(Text)
    calc_expr: Mapped[str | None] = mapped_column(Text)
    calc_time: Mapped[str | None] = mapped_column(Text)
    calc_notes: Mapped[str | None] = mapped_column(Text)
    cfs_to_gauge_converter: Mapped[str | None] = mapped_column(Text)
    cfs_to_gauge_data: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    map_name: Mapped[str | None] = mapped_column(Text)
    data_source: Mapped[str | None] = mapped_column(Text)
    db_source: Mapped[str | None] = mapped_column(Text)
    source_name: Mapped[str | None] = mapped_column(Text)
    drainage_area: Mapped[str | None] = mapped_column(Text)
    no_show: Mapped[str | None] = mapped_column(Text)
    date: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())
    user_name: Mapped[str | None] = mapped_column("username", Text)
    email: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)


class Correction(Base):
    """User-submitted corrections (replaces Corrections table)."""
    __tablename__ = "corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hash_value: Mapped[str] = mapped_column(String(8), index=True)
    approved: Mapped[str | None] = mapped_column(Text)
    random_key: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    gauge_location: Mapped[str | None] = mapped_column(Text)
    sort_key: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(Text)
    section: Mapped[str | None] = mapped_column(Text)
    drainage: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(Text)
    river_name: Mapped[str | None] = mapped_column(Text)
    river_class: Mapped[str | None] = mapped_column("class", Text)
    class_flow: Mapped[str | None] = mapped_column(Text)
    length: Mapped[str | None] = mapped_column(Text)
    gradient: Mapped[str | None] = mapped_column(Text)
    elevation_lost: Mapped[str | None] = mapped_column(Text)
    elevation: Mapped[str | None] = mapped_column(Text)
    season: Mapped[str | None] = mapped_column(Text)
    scenery: Mapped[str | None] = mapped_column(Text)
    features: Mapped[str | None] = mapped_column(Text)
    remoteness: Mapped[str | None] = mapped_column(Text)
    character: Mapped[str | None] = mapped_column("nature", Text)
    difficulties: Mapped[str | None] = mapped_column(Text)
    watershed_type: Mapped[str | None] = mapped_column(Text)
    low_flow: Mapped[str | None] = mapped_column(Text)
    high_flow: Mapped[str | None] = mapped_column(Text)
    optimal_flow: Mapped[str | None] = mapped_column(Text)
    bank_full: Mapped[str | None] = mapped_column(Text)
    flood_stage: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[str | None] = mapped_column(Text)
    longitude: Mapped[str | None] = mapped_column(Text)
    guide_book: Mapped[str | None] = mapped_column(Text)
    run_number: Mapped[str | None] = mapped_column("runnumber", Text)
    page_number: Mapped[str | None] = mapped_column("pagenumber", Text)
    station_number: Mapped[str | None] = mapped_column("stationnumber", Text)
    usgs_id: Mapped[str | None] = mapped_column(Text)
    nwsli_id: Mapped[str | None] = mapped_column(Text)
    cbtt_id: Mapped[str | None] = mapped_column(Text)
    nws_id: Mapped[str | None] = mapped_column(Text)
    geos_id: Mapped[str | None] = mapped_column(Text)
    snotel_id: Mapped[str | None] = mapped_column(Text)
    db_name: Mapped[str | None] = mapped_column(Text)
    merged_dbs: Mapped[str | None] = mapped_column(Text)
    calc_type: Mapped[str | None] = mapped_column(Text)
    calc_expr: Mapped[str | None] = mapped_column(Text)
    calc_time: Mapped[str | None] = mapped_column(Text)
    calc_notes: Mapped[str | None] = mapped_column(Text)
    cfs_to_gauge_converter: Mapped[str | None] = mapped_column(Text)
    cfs_to_gauge_data: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    map_name: Mapped[str | None] = mapped_column(Text)
    data_source: Mapped[str | None] = mapped_column(Text)
    db_source: Mapped[str | None] = mapped_column(Text)
    source_name: Mapped[str | None] = mapped_column(Text)
    drainage_area: Mapped[str | None] = mapped_column(Text)
    no_show: Mapped[str | None] = mapped_column(Text)
    date: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())
    user_name: Mapped[str | None] = mapped_column("username", Text)
    email: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)


class MergedMaster(Base):
    """Master + approved corrections (replaces MergedMaster table)."""
    __tablename__ = "merged_master"

    hash_value: Mapped[str] = mapped_column(String(8), primary_key=True)
    approved: Mapped[str | None] = mapped_column(Text)
    random_key: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    gauge_location: Mapped[str | None] = mapped_column(Text)
    sort_key: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(Text)
    section: Mapped[str | None] = mapped_column(Text)
    drainage: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(Text)
    river_name: Mapped[str | None] = mapped_column(Text)
    river_class: Mapped[str | None] = mapped_column("class", Text)
    class_flow: Mapped[str | None] = mapped_column(Text)
    length: Mapped[str | None] = mapped_column(Text)
    gradient: Mapped[str | None] = mapped_column(Text)
    elevation_lost: Mapped[str | None] = mapped_column(Text)
    elevation: Mapped[str | None] = mapped_column(Text)
    season: Mapped[str | None] = mapped_column(Text)
    scenery: Mapped[str | None] = mapped_column(Text)
    features: Mapped[str | None] = mapped_column(Text)
    remoteness: Mapped[str | None] = mapped_column(Text)
    character: Mapped[str | None] = mapped_column("nature", Text)
    difficulties: Mapped[str | None] = mapped_column(Text)
    watershed_type: Mapped[str | None] = mapped_column(Text)
    low_flow: Mapped[str | None] = mapped_column(Text)
    high_flow: Mapped[str | None] = mapped_column(Text)
    optimal_flow: Mapped[str | None] = mapped_column(Text)
    bank_full: Mapped[str | None] = mapped_column(Text)
    flood_stage: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[str | None] = mapped_column(Text)
    longitude: Mapped[str | None] = mapped_column(Text)
    guide_book: Mapped[str | None] = mapped_column(Text)
    run_number: Mapped[str | None] = mapped_column("runnumber", Text)
    page_number: Mapped[str | None] = mapped_column("pagenumber", Text)
    station_number: Mapped[str | None] = mapped_column("stationnumber", Text)
    usgs_id: Mapped[str | None] = mapped_column(Text)
    nwsli_id: Mapped[str | None] = mapped_column(Text)
    cbtt_id: Mapped[str | None] = mapped_column(Text)
    nws_id: Mapped[str | None] = mapped_column(Text)
    geos_id: Mapped[str | None] = mapped_column(Text)
    snotel_id: Mapped[str | None] = mapped_column(Text)
    db_name: Mapped[str | None] = mapped_column(Text)
    merged_dbs: Mapped[str | None] = mapped_column(Text)
    calc_type: Mapped[str | None] = mapped_column(Text)
    calc_expr: Mapped[str | None] = mapped_column(Text)
    calc_time: Mapped[str | None] = mapped_column(Text)
    calc_notes: Mapped[str | None] = mapped_column(Text)
    cfs_to_gauge_converter: Mapped[str | None] = mapped_column(Text)
    cfs_to_gauge_data: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    map_name: Mapped[str | None] = mapped_column(Text)
    data_source: Mapped[str | None] = mapped_column(Text)
    db_source: Mapped[str | None] = mapped_column(Text)
    source_name: Mapped[str | None] = mapped_column(Text)
    drainage_area: Mapped[str | None] = mapped_column(Text)
    no_show: Mapped[str | None] = mapped_column(Text)
    date: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())
    user_name: Mapped[str | None] = mapped_column("username", Text)
    email: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# levels_information supporting tables
# ---------------------------------------------------------------------------

class Parameter(Base):
    """System configuration key-value pairs (replaces Parameters table)."""
    __tablename__ = "parameters"

    ident: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)


class URLParse(Base):
    """Data source URLs and parser types (replaces URLparse table)."""
    __tablename__ = "url_parse"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    parser: Mapped[str] = mapped_column(Text, nullable=False)
    hours: Mapped[str] = mapped_column(Text, nullable=False, default="")
    inactive: Mapped[str | None] = mapped_column(Text)


class DescriptionField(Base):
    """Field display metadata for description pages (replaces Description table)."""
    __tablename__ = "description_fields"

    sort_key: Mapped[int] = mapped_column(Integer, primary_key=True)
    column_name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    prefix: Mapped[str] = mapped_column(Text, nullable=False, default="")
    suffix: Mapped[str] = mapped_column(Text, nullable=False, default="")
    info: Mapped[str | None] = mapped_column(Text)


class BuilderColumn(Base):
    """Output generation configuration (replaces Builder table)."""
    __tablename__ = "builder_columns"

    sort_key: Mapped[int] = mapped_column(Integer, primary_key=True)
    use: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    field: Mapped[str] = mapped_column(Text, nullable=False)
    length: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    name_html: Mapped[str] = mapped_column(Text, nullable=False, default="")


# ---------------------------------------------------------------------------
# levels_data tables (consolidated)
# ---------------------------------------------------------------------------

class Measurement(Base):
    """Time-series measurement data.

    Replaces the C++ pattern of dynamically creating per-station tables
    (flow_X, gage_X, etc.) with a single indexed table.
    """
    __tablename__ = "measurements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    station: Mapped[str] = mapped_column(String(255), nullable=False)
    data_type: Mapped[DataType] = mapped_column(Enum(DataType), nullable=False)
    time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint("station", "data_type", "time", name="uq_measurement"),
        Index("ix_station_type_time", "station", "data_type", "time"),
        Index("ix_station_type", "station", "data_type"),
    )


class Latest(Base):
    """Latest measurement per station/type (replaces Latest table in levels_data)."""
    __tablename__ = "latest"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    station: Mapped[str] = mapped_column(String(255), nullable=False)
    data_type: Mapped[DataType] = mapped_column(Enum(DataType), nullable=False)
    time: Mapped[datetime | None] = mapped_column(DateTime)
    value: Mapped[float | None] = mapped_column(Float)
    prev_time: Mapped[datetime | None] = mapped_column(DateTime)
    prev_value: Mapped[float | None] = mapped_column(Float)
    delta: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        UniqueConstraint("station", "data_type", name="uq_latest"),
    )


class URL2Name(Base):
    """URL source tracking (replaces url2name table in levels_data)."""
    __tablename__ = "url2name"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    time: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    url: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)


class RatingTable(Base):
    """Rating table entries for gage height <-> flow conversion.

    Replaces the dynamic {dbName}_rt tables in levels_page.
    """
    __tablename__ = "rating_tables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    db_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    feet: Mapped[float] = mapped_column(Float, nullable=False)
    cfs: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint("db_name", "feet", name="uq_rating"),
    )


# ---------------------------------------------------------------------------
# levels_page tables
# ---------------------------------------------------------------------------

class Page(Base):
    """Pre-rendered page cache (replaces Pages table in levels_page)."""
    __tablename__ = "pages"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    action: Mapped[PageAction] = mapped_column(Enum(PageAction), nullable=False)
    expires: Mapped[int | None] = mapped_column(Integer)
    modified: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())
    mimetype: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
