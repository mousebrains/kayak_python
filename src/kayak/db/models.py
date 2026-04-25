"""SQLAlchemy 2.x ORM models for the kayak database."""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
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


class EditorStatus(enum.StrEnum):
    """Editor account states.

    pending    — auto-created on first email verification; limited proposal scope.
    minimal    — maintainer-approved; can submit trip reports and photos.
    full       — maintainer-approved; can propose metadata fields (coords, class, etc.).
    banned     — soft-blocked; submissions rejected.
    maintainer — site admin; bypasses review queue, uses strong auth.
    """

    pending = "pending"
    minimal = "minimal"
    full = "full"
    banned = "banned"
    maintainer = "maintainer"


class ChangeTarget(enum.StrEnum):
    """Polymorphic target for a change_request row."""

    reach = "reach"
    gauge = "gauge"
    source = "source"
    site = "site"
    trip_report = "trip_report"


class ChangeStatus(enum.StrEnum):
    """Moderation status of a change_request row."""

    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    resolved = "resolved"
    auto_applied = "auto_applied"


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

    ``river`` / ``location`` / ``display_name`` / ``sort_name`` are populated
    by ``scripts/seed_gauge_display.py`` and are the source of truth for
    gauges.html and description pages. ``sort_name`` encodes the full row
    order (basin → fork rank → elevation DESC → DA ASC) as a single key so
    the build-time sort is plain alphabetical. Consumers fall back to the
    agency-metadata resolver only when all four columns are NULL on a gauge
    (e.g. a freshly inserted row that predates the next seeder run).
    """

    __tablename__ = "gauge"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    bank_full: Mapped[float | None] = mapped_column()
    flood_stage: Mapped[float | None] = mapped_column()
    river: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    sort_name: Mapped[str | None] = mapped_column(Text)
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
    state: Mapped[str | None] = mapped_column(Text)
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

    ``name`` is indexed but intentionally NOT unique — the same logical
    station can appear under multiple agencies (e.g. USGS + NWRFC sharing a
    station number). Callers looking up by name must use ``.first()``
    rather than ``.scalar_one_or_none()`` or risk a ``MultipleResultsFound``
    error on those stations.

    ``timezone`` is an IANA TZ name (``America/Boise``, ``America/Los_Angeles``,
    ``Etc/GMT+8``) seeded from the ``stations:`` block in data/sources.yaml
    and consumed by ``BaseParser.dump_to_db`` to localize naive timestamps
    before UTC conversion. NULL (the default) means "treat naive timestamps
    as UTC" — the correct behavior for parsers whose feed is already UTC.
    """

    __tablename__ = "source"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    agency: Mapped[str | None] = mapped_column(String(64))
    timezone: Mapped[str | None] = mapped_column(Text)
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

    Reserved for future per-gauge rating curves — ``calc-rating`` is a
    no-op until ``rating_data`` is populated for a gauge. No loader
    writes to ``rating_data`` today; see ``docs/database-schema.md``
    (Rating section) for the plan.
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
    no_show: Mapped[bool] = mapped_column(default=False, server_default=text("0"))
    map_only: Mapped[bool] = mapped_column(default=False, server_default=text("0"))
    no_flow_range: Mapped[bool] = mapped_column(default=False, server_default=text("0"))
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

    __table_args__ = (
        CheckConstraint(
            "low IS NULL OR high IS NULL OR low <= high",
            name="ck_reach_class_low_le_high",
        ),
    )


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
    sort_order: Mapped[int | None] = mapped_column()

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
# editor (Phase 1 — editor accounts for proposing changes)
# ---------------------------------------------------------------------------


class Editor(Base):
    """A user who can propose changes via the Comment / propose flow.

    Created implicitly on first magic-link verification (status='pending').
    Maintainer promotes to 'minimal' or 'full' from the admin UI to widen
    the set of fields they can edit. 'maintainer' status is seeded manually
    and uses strong auth (WebAuthn) rather than magic links.
    """

    __tablename__ = "editor"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[EditorStatus] = mapped_column(
        nullable=False, default=EditorStatus.pending, server_default="pending"
    )
    request_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column()
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("editor.id", ondelete="SET NULL"))
    last_login_at: Mapped[datetime | None] = mapped_column()

    __table_args__ = (Index("ix_editor_status", "status"),)


# ---------------------------------------------------------------------------
# editor_session (cookie-backed session token, hashed at rest)
# ---------------------------------------------------------------------------


class EditorSession(Base):
    """Session cookie record. Only sha256(cookie_value) is stored.

    Flat 7-day expiry. Logout sets revoked_at; expired/revoked rows are
    reaped lazily on lookup.
    """

    __tablename__ = "editor_session"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    editor_id: Mapped[int] = mapped_column(
        ForeignKey("editor.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column()
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    revoked_at: Mapped[datetime | None] = mapped_column()

    __table_args__ = (Index("ix_editor_session_editor_id", "editor_id"),)


# ---------------------------------------------------------------------------
# editor_magic_link (one-shot email login token)
# ---------------------------------------------------------------------------


class EditorMagicLink(Base):
    """Single-use token sent via email to verify an editor's address.

    Stored as sha256(token) so a DB leak does not permit replay. 30-min
    expiry. used_at is set on first successful consumption; subsequent
    lookups reject.
    """

    __tablename__ = "editor_magic_link"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    editor_id: Mapped[int] = mapped_column(
        ForeignKey("editor.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    used_at: Mapped[datetime | None] = mapped_column()
    ip_issued: Mapped[str | None] = mapped_column(String(45))
    next_url: Mapped[str | None] = mapped_column(String(512))

    __table_args__ = (Index("ix_editor_magic_link_editor_id", "editor_id"),)


# ---------------------------------------------------------------------------
# maintainer_credential (WebAuthn passkey — Phase 1b)
# ---------------------------------------------------------------------------


class MaintainerCredential(Base):
    """WebAuthn (passkey) credential for a maintainer.

    Phase 1 ships the table; Phase 1b wires registration + assertion.
    One maintainer may enroll multiple credentials (phone, laptop, backup YubiKey).
    """

    __tablename__ = "maintainer_credential"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    editor_id: Mapped[int] = mapped_column(
        ForeignKey("editor.id", ondelete="CASCADE"), nullable=False
    )
    credential_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    sign_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    transports: Mapped[str | None] = mapped_column(String(128))
    nickname: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column()
    revoked_at: Mapped[datetime | None] = mapped_column()

    __table_args__ = (Index("ix_maintainer_credential_editor_id", "editor_id"),)


# ---------------------------------------------------------------------------
# change_request (polymorphic proposal queue)
# ---------------------------------------------------------------------------


class ChangeRequest(Base):
    """A proposed change to a reach, gauge, source, or a site-level comment.

    payload_json shape depends on target_type (see design doc). Only
    maintainer approval writes the change into the live tables; the
    applied_json column captures exactly what was written after any
    maintainer edits.
    """

    __tablename__ = "change_request"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    target_type: Mapped[ChangeTarget] = mapped_column(nullable=False)
    target_id: Mapped[int | None] = mapped_column()
    editor_id: Mapped[int] = mapped_column(
        ForeignKey("editor.id", ondelete="CASCADE"), nullable=False
    )
    submitted_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    subject: Mapped[str | None] = mapped_column(String(256))
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    notes_to_maint: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ChangeStatus] = mapped_column(
        nullable=False, default=ChangeStatus.pending, server_default="pending"
    )
    reviewed_at: Mapped[datetime | None] = mapped_column()
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("editor.id", ondelete="SET NULL"))
    reviewer_note: Mapped[str | None] = mapped_column(Text)
    applied_json: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_change_request_status", "status"),
        Index("ix_change_request_target", "target_type", "target_id"),
        Index("ix_change_request_editor_id", "editor_id"),
    )


# ---------------------------------------------------------------------------
# change_request_attachment (photos; Phase 4)
# ---------------------------------------------------------------------------


class ChangeRequestAttachment(Base):
    """Uploaded binary attached to a change_request (photos for trip reports).

    Phase 1 ships the schema; no upload endpoint yet. Binaries live on
    disk under a dedicated uploads root, keyed by sha256 filename.
    """

    __tablename__ = "change_request_attachment"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    change_request_id: Mapped[int] = mapped_column(
        ForeignKey("change_request.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    caption: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("change_request_id", "sha256", name="uq_attachment_request_sha"),
        Index("ix_attachment_change_request_id", "change_request_id"),
    )


# ---------------------------------------------------------------------------
# edit_history (post-apply changelog)
# ---------------------------------------------------------------------------


class EditHistory(Base):
    """Audit trail of fields actually written to the live tables.

    One row per (target_type, target_id, field). Populated both by the
    maintainer's direct edit path (/edit.php) and by approval of a
    change_request. changed_by is either 'maintainer:<editor_id>' or
    'editor:<editor_id>'.
    """

    __tablename__ = "edit_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    target_type: Mapped[ChangeTarget] = mapped_column(nullable=False)
    target_id: Mapped[int | None] = mapped_column()
    change_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("change_request.id", ondelete="SET NULL")
    )
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    changed_by: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        Index("ix_edit_history_target", "target_type", "target_id"),
        Index("ix_edit_history_changed_at", "changed_at"),
    )


# ---------------------------------------------------------------------------
# huc_name — human-readable labels for HUC8/10/12 codes (NHD WBD lookup)
# ---------------------------------------------------------------------------


class HucName(Base):
    """Watershed-name lookup for the codes stored in `reach.huc` (HUC12).

    Populated from the WBD layers shipped with NHDPlus HR. Coarser levels are
    derived from `reach.huc` via `substr(huc, 1, N)`; this table provides the
    human-readable name for any of those prefixes.
    """

    __tablename__ = "huc_name"

    code: Mapped[str] = mapped_column(String(12), primary_key=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    states: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_huc_name_level", "level"),)
