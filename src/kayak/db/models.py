"""SQLAlchemy 2.x ORM models for the kayak database."""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

# ---------------------------------------------------------------------------
# ENUMs
# ---------------------------------------------------------------------------


class DataType(enum.StrEnum):
    """Measurement types."""

    gauge = "gauge"
    flow = "flow"
    inflow = "inflow"
    temperature = "temperature"


class FlowLevel(enum.StrEnum):
    """Flow level classifications for reach_level."""

    low = "low"
    okay = "okay"
    high = "high"


class PageAction(enum.StrEnum):
    """Page cache action types."""

    PAGE = "page"
    FILE = "file"
    PLOT = "plot"
    VIEW = "view"
    EDIT = "edit"
    SVG = "svg"
    PNG = "png"


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# gauge
# ---------------------------------------------------------------------------


class Gauge(Base):
    """Physical gauge station on a river.

    Stores location metadata and agency-specific IDs (USGS, NWS, CBTT, etc.).
    Linked to data sources via the gauge_source M2M table and to a rating table
    for gage-height-to-flow conversions.
    """

    __tablename__ = "gauge"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    bank_full: Mapped[float | None] = mapped_column()
    flood_stage: Mapped[float | None] = mapped_column()
    location: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    elevation: Mapped[float | None] = mapped_column()
    drainage_area: Mapped[float | None] = mapped_column()
    station_id: Mapped[str | None] = mapped_column(Text)
    cbtt_id: Mapped[str | None] = mapped_column(Text)
    geos_id: Mapped[str | None] = mapped_column(Text)
    nws_id: Mapped[str | None] = mapped_column(Text)
    nwsli_id: Mapped[str | None] = mapped_column(Text)
    snotel_id: Mapped[str | None] = mapped_column(Text)
    usgs_id: Mapped[str | None] = mapped_column(String(32))
    huc: Mapped[str | None] = mapped_column(Text)
    allow_negative_flow: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0")
    )
    rating_id: Mapped[int | None] = mapped_column(ForeignKey("rating.id", ondelete="SET NULL"))

    # relationships
    rating: Mapped[Rating | None] = relationship(back_populates="gauges")
    sources: Mapped[list[Source]] = relationship(secondary="gauge_source", back_populates="gauges")
    reaches: Mapped[list[Reach]] = relationship(back_populates="gauge")

    __table_args__ = (Index("ix_gauge_usgs_id", "usgs_id"),)


# ---------------------------------------------------------------------------
# source
# ---------------------------------------------------------------------------


class Source(Base):
    """A data feed providing observations for one or more gauges.

    Each source is either fetched from a remote URL (via fetch_url) or
    calculated from other sources (via calc_expression). Multiple sources
    may feed the same gauge, with observations merged by the merge step.
    """

    __tablename__ = "source"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    agency: Mapped[str | None] = mapped_column(String(64))
    fetch_url_id: Mapped[int | None] = mapped_column(
        ForeignKey("fetch_url.id", ondelete="SET NULL")
    )
    calc_expression_id: Mapped[int | None] = mapped_column(
        ForeignKey("calc_expression.id", ondelete="SET NULL")
    )

    # relationships
    fetch_url: Mapped[FetchUrl | None] = relationship(back_populates="sources")
    calc_expression: Mapped[CalcExpression | None] = relationship(back_populates="sources")
    gauges: Mapped[list[Gauge]] = relationship(secondary="gauge_source", back_populates="sources")
    observations: Mapped[list[Observation]] = relationship(back_populates="source")
    latest_observations: Mapped[list[LatestObservation]] = relationship(back_populates="source")

    __table_args__ = (Index("ix_source_name", "name"),)


# ---------------------------------------------------------------------------
# gauge_source (M2M junction)
# ---------------------------------------------------------------------------


class GaugeSource(Base):
    """Many-to-many junction linking gauges to their data sources."""

    __tablename__ = "gauge_source"

    gauge_id: Mapped[int] = mapped_column(
        ForeignKey("gauge.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("source.id", ondelete="CASCADE"), primary_key=True
    )


# ---------------------------------------------------------------------------
# fetch_url
# ---------------------------------------------------------------------------


class FetchUrl(Base):
    """Remote URL to fetch observation data from.

    Seeded from data/sources.yaml by init-db. The ``parser`` field names
    the registered parser class. The ``hours`` field restricts which hours
    of the day this URL should be fetched (e.g. "6,12,18").
    """

    __tablename__ = "fetch_url"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    parser: Mapped[str | None] = mapped_column(String(32))
    hours: Mapped[str | None] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(default=False, server_default=text("0"))
    last_fetched_at: Mapped[datetime | None] = mapped_column()

    # relationships
    sources: Mapped[list[Source]] = relationship(back_populates="fetch_url")

    __table_args__ = (Index("ix_fetch_url_is_active", "is_active"),)


# ---------------------------------------------------------------------------
# calc_expression
# ---------------------------------------------------------------------------


class CalcExpression(Base):
    """Formula for computing synthetic observations from other sources.

    Expressions reference gauge values by name (e.g. ``Gauge Name::flow``)
    and are evaluated by the calculator pipeline step. Dependencies are
    resolved via topological sort.
    """

    __tablename__ = "calc_expression"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    data_type: Mapped[DataType] = mapped_column(nullable=False)
    expression: Mapped[str] = mapped_column(String(512), nullable=False)
    time_expression: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)

    # relationships
    sources: Mapped[list[Source]] = relationship(back_populates="calc_expression")


# ---------------------------------------------------------------------------
# rating
# ---------------------------------------------------------------------------


class Rating(Base):
    """Gage-height-to-flow conversion table for a gauge.

    Contains a URL for the rating source and a set of RatingData points
    used for linear interpolation by the calc-rating pipeline step.
    """

    __tablename__ = "rating"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    url: Mapped[str | None] = mapped_column(String(512))
    parser: Mapped[str | None] = mapped_column(String(32))

    # relationships
    gauges: Mapped[list[Gauge]] = relationship(back_populates="rating")
    data_points: Mapped[list[RatingData]] = relationship(back_populates="rating")


# ---------------------------------------------------------------------------
# rating_data
# ---------------------------------------------------------------------------


class RatingData(Base):
    """Single (gage_height_ft, flow_cfs) data point in a rating table.

    Points must be ordered by gauge_height_ft for interpolation to work.
    """

    __tablename__ = "rating_data"

    rating_id: Mapped[int] = mapped_column(
        ForeignKey("rating.id", ondelete="CASCADE"), primary_key=True
    )
    gauge_height_ft: Mapped[float] = mapped_column(primary_key=True)
    flow_cfs: Mapped[float] = mapped_column(nullable=False)

    # relationships
    rating: Mapped[Rating] = relationship(back_populates="data_points")


# ---------------------------------------------------------------------------
# observation
# ---------------------------------------------------------------------------


class Observation(Base):
    """Time-series measurement from a data source.

    Primary key is (source_id, observed_at, data_type). This is the largest
    table in the database (~5M+ rows). Old rows are thinned by the decimate
    command using the LTTB algorithm.
    """

    __tablename__ = "observation"

    source_id: Mapped[int] = mapped_column(
        ForeignKey("source.id", ondelete="RESTRICT"), primary_key=True
    )
    observed_at: Mapped[datetime] = mapped_column(primary_key=True)
    data_type: Mapped[DataType] = mapped_column(primary_key=True)
    value: Mapped[float] = mapped_column(nullable=False)

    # relationships
    source: Mapped[Source] = relationship(back_populates="observations")

    __table_args__ = (
        Index("ix_observation_source_type_time", "source_id", "data_type", "observed_at"),
    )


# ---------------------------------------------------------------------------
# latest_observation (cache table)
# ---------------------------------------------------------------------------


class LatestObservation(Base):
    """Cached most-recent observation per (source_id, data_type).

    Also stores the previous value from ~6 hours ago and the computed
    delta_per_hour for trend display. Updated by store_observation().
    """

    __tablename__ = "latest_observation"

    source_id: Mapped[int] = mapped_column(
        ForeignKey("source.id", ondelete="RESTRICT"), primary_key=True
    )
    data_type: Mapped[DataType] = mapped_column(primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(nullable=False)
    value: Mapped[float] = mapped_column(nullable=False)
    prev_observed_at: Mapped[datetime | None] = mapped_column()
    prev_value: Mapped[float | None] = mapped_column()
    delta_per_hour: Mapped[float | None] = mapped_column()

    # relationships
    source: Mapped[Source] = relationship(back_populates="latest_observations")


# ---------------------------------------------------------------------------
# latest_gauge_observation (gauge-level cache table)
# ---------------------------------------------------------------------------


class LatestGaugeObservation(Base):
    """Gauge-level consolidated latest values across all sources.

    For gauges with multiple sources, this picks the best (most recent)
    observation. Used by the build step to generate the HTML tables.
    """

    __tablename__ = "latest_gauge_observation"

    gauge_id: Mapped[int] = mapped_column(
        ForeignKey("gauge.id", ondelete="CASCADE"), primary_key=True
    )
    data_type: Mapped[DataType] = mapped_column(primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(nullable=False)
    value: Mapped[float] = mapped_column(nullable=False)
    prev_observed_at: Mapped[datetime | None] = mapped_column()
    prev_value: Mapped[float | None] = mapped_column()
    delta_per_hour: Mapped[float | None] = mapped_column()
    source_id: Mapped[int | None] = mapped_column(ForeignKey("source.id", ondelete="SET NULL"))

    # relationships
    gauge: Mapped[Gauge] = relationship()


# ---------------------------------------------------------------------------
# reach
# ---------------------------------------------------------------------------


class Reach(Base):
    """A paddleable section of river with metadata, levels, and coordinates.

    Linked to a gauge for live data, to states via reach_state, and to
    guidebooks via reach_guidebook. The ``geom`` field stores WKT LineString
    geometry for map display.
    """

    __tablename__ = "reach"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    updated_at: Mapped[datetime | None] = mapped_column()
    gauge_id: Mapped[int | None] = mapped_column(ForeignKey("gauge.id", ondelete="SET NULL"))
    name: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    sort_name: Mapped[str | None] = mapped_column(String(256))
    nature: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    difficulties: Mapped[str | None] = mapped_column(Text)
    basin: Mapped[str | None] = mapped_column(Text)
    basin_area: Mapped[float | None] = mapped_column()
    elevation: Mapped[float | None] = mapped_column()
    elevation_lost: Mapped[float | None] = mapped_column()
    length: Mapped[float | None] = mapped_column()
    gradient: Mapped[float | None] = mapped_column()
    features: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    latitude_start: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude_start: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    latitude_end: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude_end: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    map_name: Mapped[str | None] = mapped_column(Text)
    no_show: Mapped[bool] = mapped_column(default=False, server_default=text("0"))
    map_only: Mapped[bool] = mapped_column(default=False, server_default=text("0"))
    notes: Mapped[str | None] = mapped_column(Text)
    optimal_flow: Mapped[float | None] = mapped_column()
    region: Mapped[str | None] = mapped_column(Text)
    remoteness: Mapped[str | None] = mapped_column(Text)
    scenery: Mapped[str | None] = mapped_column(Text)
    season: Mapped[str | None] = mapped_column(Text)
    watershed_type: Mapped[str | None] = mapped_column(Text)
    aw_id: Mapped[int | None] = mapped_column()
    river: Mapped[str | None] = mapped_column(Text)
    max_gradient: Mapped[float | None] = mapped_column()
    geom: Mapped[str | None] = mapped_column(Text)
    huc: Mapped[str | None] = mapped_column(Text)

    # relationships
    gauge: Mapped[Gauge | None] = relationship(back_populates="reaches")
    states: Mapped[list[State]] = relationship(secondary="reach_state", back_populates="reaches")
    classes: Mapped[list[ReachClass]] = relationship(back_populates="reach")
    levels: Mapped[list[ReachLevel]] = relationship(back_populates="reach")
    guidebooks: Mapped[list[Guidebook]] = relationship(
        secondary="reach_guidebook", back_populates="reaches"
    )

    __table_args__ = (Index("ix_reach_sort_name", "sort_name"),)


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------


class State(Base):
    """US state. Reaches are linked to states via the reach_state junction."""

    __tablename__ = "state"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    abbreviation: Mapped[str | None] = mapped_column(String(2))

    # relationships
    reaches: Mapped[list[Reach]] = relationship(secondary="reach_state", back_populates="states")


# ---------------------------------------------------------------------------
# reach_state (M2M junction)
# ---------------------------------------------------------------------------


class ReachState(Base):
    """Many-to-many junction linking reaches to states."""

    __tablename__ = "reach_state"

    reach_id: Mapped[int] = mapped_column(
        ForeignKey("reach.id", ondelete="CASCADE"), primary_key=True
    )
    state_id: Mapped[int] = mapped_column(
        ForeignKey("state.id", ondelete="CASCADE"), primary_key=True
    )

    __table_args__ = (Index("ix_reach_state_state_id", "state_id"),)


# ---------------------------------------------------------------------------
# reach_class
# ---------------------------------------------------------------------------


class ReachClass(Base):
    """Whitewater difficulty class for a reach (e.g. III, IV+).

    Optionally bounded by flow/gage thresholds: the class applies when
    the observed value is between ``low`` and ``high``.
    """

    __tablename__ = "reach_class"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    reach_id: Mapped[int] = mapped_column(
        ForeignKey("reach.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    low: Mapped[float | None] = mapped_column()
    low_data_type: Mapped[DataType | None] = mapped_column()
    high: Mapped[float | None] = mapped_column()
    high_data_type: Mapped[DataType | None] = mapped_column()

    # relationships
    reach: Mapped[Reach] = relationship(back_populates="classes")


# ---------------------------------------------------------------------------
# reach_level
# ---------------------------------------------------------------------------


class ReachLevel(Base):
    """Flow level classification (low/okay/high) for a reach.

    Defines the threshold ranges used to color-code the levels table.
    A reach may have multiple level rows (one per FlowLevel).
    """

    __tablename__ = "reach_level"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    reach_id: Mapped[int] = mapped_column(
        ForeignKey("reach.id", ondelete="CASCADE"), nullable=False
    )
    level: Mapped[FlowLevel] = mapped_column(nullable=False)
    low: Mapped[float | None] = mapped_column()
    low_data_type: Mapped[DataType | None] = mapped_column()
    high: Mapped[float | None] = mapped_column()
    high_data_type: Mapped[DataType | None] = mapped_column()

    # relationships
    reach: Mapped[Reach] = relationship(back_populates="levels")


# ---------------------------------------------------------------------------
# class_description
# ---------------------------------------------------------------------------


class ClassDescription(Base):
    """Human-readable description of a whitewater difficulty class."""

    __tablename__ = "class_description"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)


# ---------------------------------------------------------------------------
# guidebook
# ---------------------------------------------------------------------------


class Guidebook(Base):
    """Published guidebook that references river reaches (e.g. Soggy Sneakers)."""

    __tablename__ = "guidebook"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(256))
    edition: Mapped[str | None] = mapped_column(String(24))
    author: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)

    # relationships
    reaches: Mapped[list[Reach]] = relationship(
        secondary="reach_guidebook", back_populates="guidebooks"
    )


# ---------------------------------------------------------------------------
# reach_guidebook (M2M junction with extra columns)
# ---------------------------------------------------------------------------


class ReachGuidebook(Base):
    """Many-to-many junction linking reaches to guidebooks with page/run/URL."""

    __tablename__ = "reach_guidebook"

    reach_id: Mapped[int] = mapped_column(
        ForeignKey("reach.id", ondelete="CASCADE"), primary_key=True
    )
    guidebook_id: Mapped[int] = mapped_column(
        ForeignKey("guidebook.id", ondelete="CASCADE"), primary_key=True
    )
    page: Mapped[str | None] = mapped_column(Text)
    run: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# page (cache table — kept from original schema)
# ---------------------------------------------------------------------------


class Page(Base):
    """Pre-rendered page cache."""

    __tablename__ = "pages"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    action: Mapped[PageAction] = mapped_column(nullable=False)
    expires: Mapped[int | None] = mapped_column(Integer)
    modified: Mapped[datetime | None] = mapped_column(default=func.now())
    mimetype: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
