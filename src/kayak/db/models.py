"""SQLAlchemy 2.x ORM models for the wkcclevels database.

Normalized schema with 18 tables (16 from production + Page/PageAction).
Replaces the flat Master/MergedMaster/Correction schema with proper
Reach/Gauge/Source relationships.
"""

from __future__ import annotations

import enum
import sys
from datetime import datetime
from decimal import Decimal

# Python 3.10 compatibility: StrEnum was added in 3.11
if sys.version_info < (3, 11):
    class _StrEnum(str, enum.Enum):
        pass
    enum.StrEnum = _StrEnum

from sqlalchemy import (
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
    """Measurement types (replaces DataDB::TYPE)."""
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
    """Page cache action types (replaces PageDB::ACTION)."""
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
    rating_id: Mapped[int | None] = mapped_column(
        ForeignKey("rating.id", ondelete="SET NULL")
    )

    # relationships
    rating: Mapped[Rating | None] = relationship(back_populates="gauges")
    sources: Mapped[list[Source]] = relationship(
        secondary="gauge_source", back_populates="gauges"
    )
    reaches: Mapped[list[Reach]] = relationship(back_populates="gauge")

    __table_args__ = (
        Index("ix_gauge_usgs_id", "usgs_id"),
    )


# ---------------------------------------------------------------------------
# source
# ---------------------------------------------------------------------------

class Source(Base):
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
    calc_expression: Mapped[CalcExpression | None] = relationship(
        back_populates="sources"
    )
    gauges: Mapped[list[Gauge]] = relationship(
        secondary="gauge_source", back_populates="sources"
    )
    observations: Mapped[list[Observation]] = relationship(back_populates="source")
    latest_observations: Mapped[list[LatestObservation]] = relationship(
        back_populates="source"
    )

    __table_args__ = (
        Index("ix_source_name", "name"),
    )


# ---------------------------------------------------------------------------
# gauge_source (M2M junction)
# ---------------------------------------------------------------------------

class GaugeSource(Base):
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
    __tablename__ = "fetch_url"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    parser: Mapped[str | None] = mapped_column(String(32))
    hours: Mapped[str | None] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(default=False, server_default=text("0"))
    last_fetched_at: Mapped[datetime | None] = mapped_column()

    # relationships
    sources: Mapped[list[Source]] = relationship(back_populates="fetch_url")

    __table_args__ = (
        Index("ix_fetch_url_is_active", "is_active"),
    )


# ---------------------------------------------------------------------------
# calc_expression
# ---------------------------------------------------------------------------

class CalcExpression(Base):
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
# reach
# ---------------------------------------------------------------------------

class Reach(Base):
    __tablename__ = "reach"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    updated_at: Mapped[datetime | None] = mapped_column()
    gauge_id: Mapped[int | None] = mapped_column(
        ForeignKey("gauge.id", ondelete="SET NULL")
    )
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

    # relationships
    gauge: Mapped[Gauge | None] = relationship(back_populates="reaches")
    states: Mapped[list[State]] = relationship(
        secondary="reach_state", back_populates="reaches"
    )
    classes: Mapped[list[ReachClass]] = relationship(back_populates="reach")
    levels: Mapped[list[ReachLevel]] = relationship(back_populates="reach")
    guidebooks: Mapped[list[Guidebook]] = relationship(
        secondary="reach_guidebook", back_populates="reaches"
    )

    __table_args__ = (
        Index("ix_reach_sort_name", "sort_name"),
    )


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

class State(Base):
    __tablename__ = "state"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    abbreviation: Mapped[str | None] = mapped_column(String(2))

    # relationships
    reaches: Mapped[list[Reach]] = relationship(
        secondary="reach_state", back_populates="states"
    )


# ---------------------------------------------------------------------------
# reach_state (M2M junction)
# ---------------------------------------------------------------------------

class ReachState(Base):
    __tablename__ = "reach_state"

    reach_id: Mapped[int] = mapped_column(
        ForeignKey("reach.id", ondelete="CASCADE"), primary_key=True
    )
    state_id: Mapped[int] = mapped_column(
        ForeignKey("state.id", ondelete="CASCADE"), primary_key=True
    )

    __table_args__ = (
        Index("ix_reach_state_state_id", "state_id"),
    )


# ---------------------------------------------------------------------------
# reach_class
# ---------------------------------------------------------------------------

class ReachClass(Base):
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
    __tablename__ = "class_description"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)


# ---------------------------------------------------------------------------
# guidebook
# ---------------------------------------------------------------------------

class Guidebook(Base):
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
    """Pre-rendered page cache (replaces Pages table in levels_page)."""
    __tablename__ = "pages"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    action: Mapped[PageAction] = mapped_column(nullable=False)
    expires: Mapped[int | None] = mapped_column(Integer)
    modified: Mapped[datetime | None] = mapped_column(default=func.now())
    mimetype: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
