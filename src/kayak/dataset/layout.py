"""Dataset layout descriptor — the single source of truth for a dataset's shape.

S4a introduces this so the validator, the fixture generator (and later
``init-dataset`` / ``generate-sources``) all agree on which files a dataset
must contain, the column set and per-column type of each CSV, which tables
carry stable ids, and which columns live in the geometry/gradient JSON
sidecars rather than in ``reach.csv``. S6 promotes this descriptor into the
versioned contract manifest; until then it is the contract.

A dataset is a **complete projection** of the metadata schema: every
``CONTRACT_CSVS`` file is required (header-only when the table is empty) plus
both JSON sidecars (``{}`` when empty), so the validator treats a missing file
as corruption, not as "this table is not applicable" — absence and emptiness are
distinct, and emptiness is expressed by a header-only CSV / empty-object JSON.
The files come from two writers: ``scripts/export_metadata.py`` (the nightly DB
snapshot) writes ``SNAPSHOT_EXPORT_CSVS``, while the generator-owned
``GENERATOR_OWNED_CSVS`` trio (source/fetch_url/gauge_source) is written only by
``levels generate-sources`` from the dataset's ``sources.yaml`` — so the snapshot
never races them (dataset-separation S1's "no dual-writer window"). Both writers
together still produce every contract file.

CSV column sets and types are derived from the SQLAlchemy models minus the
columns that export to JSON sidecars or are runtime churn — exactly the rule
``scripts/export_metadata.py`` writes by — so the descriptor cannot drift from
the schema. Headers are validated as a **set** (the loaders key by column name;
column order is not semantically meaningful) after rejecting duplicate names.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa

from kayak.db.models import Base

# Columns present on the model but NOT written to the table's CSV: large
# machine-generated geometry/gradient (→ JSON sidecars) and runtime churn.
# Mirrors scripts/export_metadata.py::EXCLUDED_COLUMNS.
EXCLUDED_COLUMNS: dict[str, set[str]] = {
    "reach": {"geom", "gradient_profile"},
    "fetch_url": {"last_fetched_at"},
}

# Nullable columns a CSV MAY omit (vs. the complete-projection default that every
# model column is present). `unknown_station_policy` is the per-URL opt-in added
# in S1: a dataset that wants the all-reject default simply leaves the column out,
# so `generate-sources` omits it when no URL opts in. This keeps the column's
# introduction backward-compatible — an older committed `fetch_url.csv` (no
# column) stays valid and byte-stable under `generate-sources --check`, which the
# base-pin CI trust boundary requires (a dataset can't validate against the new
# engine in the same PR that bumps the pin). When the column IS present it is
# validated like any other.
OPTIONAL_COLUMNS: dict[str, set[str]] = {
    "fetch_url": {"unknown_station_policy"},
}

# Every metadata table a dataset projects to a CSV, in export order
# (scripts/export_metadata.py::METADATA_TABLES). A complete projection carries
# all of them — header-only when a table has no rows — so a missing file is
# detectable corruption rather than silent optionality.
CONTRACT_CSVS: tuple[str, ...] = (
    "state",
    "source",
    "gauge",
    "gauge_source",
    "fetch_url",
    "calc_expression",
    "rating",
    "rating_data",
    "class_description",
    "guidebook",
    "reach",
    "reach_state",
    "reach_class",
    "reach_guidebook",
    "huc_name",
)
# Any CSV in the dataset directory whose stem is not one of these is unexpected.
KNOWN_CSVS: tuple[str, ...] = CONTRACT_CSVS

# CSVs whose sole writer is ``levels generate-sources`` (projected from the
# dataset's ``sources.yaml``), NOT the nightly DB snapshot. The snapshot
# (``scripts/export_metadata.py``) must not export these — otherwise two writers
# race the same files and a fetch-time DB change could drift them out of the
# byte-for-byte ``generate-sources --check`` (dataset-separation S1, "no
# dual-writer window"). They stay in ``CONTRACT_CSVS`` — still required, still
# applied by ``levels sync-metadata`` — only the *export* side excludes them.
GENERATOR_OWNED_CSVS: frozenset[str] = frozenset({"source", "fetch_url", "gauge_source"})

# Tables the nightly snapshot exports: every contract CSV except the
# generator-owned trio, in contract order.
SNAPSHOT_EXPORT_CSVS: tuple[str, ...] = tuple(
    t for t in CONTRACT_CSVS if t not in GENERATOR_OWNED_CSVS
)

# id_counters.csv is required and is itself not a contract table (no id column).
ID_COUNTERS_CSV = "id_counters.csv"

# JSON sidecars keyed by reach id — both always present (``{}`` when empty).
GEOM_JSON = "reaches.json"  # required: every reach must carry geometry
GRADIENT_JSON = "reaches-gradient.json"  # required file; per-reach entries optional

# Child CSVs whose reach_id must reference an existing reach.
REACH_CHILD_CSVS: tuple[str, ...] = ("reach_state", "reach_class", "reach_guidebook")


# Magnitude bounds for the geographic-coordinate columns, a domain check on top
# of the Numeric(9, 6) precision/scale: a value can fit the scale yet still be
# an impossible coordinate (e.g. latitude 95), and an absurd value (1e300) is
# caught here too. export_metadata rounds coordinates to 6 dp on write so the
# declared scale is actually met.
_LAT_RANGE = (-90.0, 90.0)
_LON_RANGE = (-180.0, 180.0)


@dataclass(frozen=True)
class ColumnSpec:
    """A CSV column's validation contract, derived from its ORM column."""

    name: str
    kind: str  # "int" | "number" | "bool" | "datetime" | "date" | "enum" | "text"
    nullable: bool
    enums: tuple[str, ...] = ()
    max_length: int | None = None  # String(n) cap for text columns
    is_id: bool = False  # the id PK or an FK to some table's id (stable handle)
    value_range: tuple[float, float] | None = None  # magnitude bound (coordinates)
    decimal_spec: tuple[int, int] | None = None  # Numeric(precision, scale)


def _kind(coltype: sa.types.TypeEngine) -> tuple[str, tuple[str, ...]]:
    """Classify a SQLAlchemy column type into a CSV value kind (+ enum members).

    Order matters: ``Enum`` subclasses ``String`` and ``Float`` subclasses
    ``Numeric``, so the more specific types are tested first.
    """
    if isinstance(coltype, sa.Enum):
        return "enum", tuple(coltype.enums)
    if isinstance(coltype, sa.Boolean):
        return "bool", ()
    if isinstance(coltype, sa.Integer):
        return "int", ()
    if isinstance(coltype, (sa.Float, sa.Numeric)):
        return "number", ()
    if isinstance(coltype, sa.DateTime):
        return "datetime", ()
    if isinstance(coltype, sa.Date):
        return "date", ()
    return "text", ()  # String / Text / anything else: no value-format constraint


def _coordinate_range(name: str) -> tuple[float, float] | None:
    if "latitude" in name:
        return _LAT_RANGE
    if "longitude" in name:
        return _LON_RANGE
    return None


def expected_columns(table: str) -> set[str]:
    """The full CSV column-name set for ``table`` (model columns minus excluded).

    Includes :data:`OPTIONAL_COLUMNS` — a CSV may carry them but is not required
    to; use :func:`optional_columns` to split required from optional.
    """
    cols = {c.name for c in Base.metadata.tables[table].columns}
    return cols - EXCLUDED_COLUMNS.get(table, set())


def optional_columns(table: str) -> set[str]:
    """Columns ``table``'s CSV MAY omit (see :data:`OPTIONAL_COLUMNS`)."""
    return OPTIONAL_COLUMNS.get(table, set())


def ordered_columns(table: str) -> list[str]:
    """CSV columns in model-definition order (for deterministic writers)."""
    excluded = EXCLUDED_COLUMNS.get(table, set())
    return [c.name for c in Base.metadata.tables[table].columns if c.name not in excluded]


def primary_key_columns(table: str) -> list[str]:
    """The CSV columns forming ``table``'s primary key (single ``id`` or composite)."""
    return [c.name for c in Base.metadata.tables[table].primary_key.columns]


def column_specs(table: str) -> list[ColumnSpec]:
    """Per-column validation contract for ``table``'s CSV, in model order."""
    excluded = EXCLUDED_COLUMNS.get(table, set())
    specs: list[ColumnSpec] = []
    for c in Base.metadata.tables[table].columns:
        if c.name in excluded:
            continue
        kind, enums = _kind(c.type)
        max_length = c.type.length if (kind == "text" and isinstance(c.type, sa.String)) else None
        is_id = c.name == "id" or any(fk.target_fullname.endswith(".id") for fk in c.foreign_keys)
        value_range = _coordinate_range(c.name) if kind == "number" else None
        decimal_spec = _decimal_spec(c.type)
        specs.append(
            ColumnSpec(
                name=c.name,
                kind=kind,
                nullable=bool(c.nullable),
                enums=enums,
                max_length=max_length,
                is_id=is_id,
                value_range=value_range,
                decimal_spec=decimal_spec,
            )
        )
    return specs


def _decimal_spec(coltype: sa.types.TypeEngine) -> tuple[int, int] | None:
    """``(precision, scale)`` for a fixed-precision ``Numeric`` column (not Float)."""
    if (
        isinstance(coltype, sa.Numeric)
        and not isinstance(coltype, sa.Float)
        and coltype.precision is not None
        and coltype.scale is not None
    ):
        return coltype.precision, coltype.scale
    return None


def id_bearing_tables() -> set[str]:
    """Known tables whose primary key is a single ``id`` column — each must have
    exactly one ``id_counters`` entry."""
    out: set[str] = set()
    for table in CONTRACT_CSVS:
        pk = [c.name for c in Base.metadata.tables[table].primary_key.columns]
        if pk == ["id"]:
            out.add(table)
    return out
