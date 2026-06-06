"""Dataset layout descriptor — the single source of truth for a dataset's shape.

S4a introduces this so the validator, the fixture generator (and later
``init-dataset`` / ``generate-sources``) all agree on which files a dataset
must contain, the column set and per-column type of each CSV, which tables
carry stable ids, and which columns live in the geometry/gradient JSON
sidecars rather than in ``reach.csv``. S6 promotes this descriptor into the
versioned contract manifest; until then it is the contract.

A dataset is a **complete projection** of the metadata schema: ``levels build``
/ ``scripts/export_metadata.py`` write one CSV per metadata table (header-only
when the table is empty) plus both JSON sidecars (``{}`` when empty), so a
faithfully-exported dataset always carries *every* contract file. The validator
therefore treats a missing file as corruption, not as "this table is not
applicable" — absence and emptiness are distinct, and emptiness is expressed by
a header-only CSV / empty-object JSON.

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

# id_counters.csv is required and is itself not a contract table (no id column).
ID_COUNTERS_CSV = "id_counters.csv"

# JSON sidecars keyed by reach id — both always present (``{}`` when empty).
GEOM_JSON = "reaches.json"  # required: every reach must carry geometry
GRADIENT_JSON = "reaches-gradient.json"  # required file; per-reach entries optional

# Child CSVs whose reach_id must reference an existing reach.
REACH_CHILD_CSVS: tuple[str, ...] = ("reach_state", "reach_class", "reach_guidebook")


@dataclass(frozen=True)
class ColumnSpec:
    """A CSV column's validation contract, derived from its ORM column."""

    name: str
    kind: str  # "int" | "number" | "bool" | "datetime" | "date" | "enum" | "text"
    nullable: bool
    enums: tuple[str, ...] = ()


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


def expected_columns(table: str) -> set[str]:
    """The exact CSV column-name set for ``table`` (model columns minus excluded)."""
    cols = {c.name for c in Base.metadata.tables[table].columns}
    return cols - EXCLUDED_COLUMNS.get(table, set())


def ordered_columns(table: str) -> list[str]:
    """CSV columns in model-definition order (for deterministic writers)."""
    excluded = EXCLUDED_COLUMNS.get(table, set())
    return [c.name for c in Base.metadata.tables[table].columns if c.name not in excluded]


def column_specs(table: str) -> list[ColumnSpec]:
    """Per-column validation contract for ``table``'s CSV, in model order."""
    excluded = EXCLUDED_COLUMNS.get(table, set())
    specs: list[ColumnSpec] = []
    for c in Base.metadata.tables[table].columns:
        if c.name in excluded:
            continue
        kind, enums = _kind(c.type)
        specs.append(ColumnSpec(name=c.name, kind=kind, nullable=bool(c.nullable), enums=enums))
    return specs


def id_bearing_tables() -> set[str]:
    """Known tables whose primary key is a single ``id`` column — each must have
    exactly one ``id_counters`` entry."""
    out: set[str] = set()
    for table in CONTRACT_CSVS:
        pk = [c.name for c in Base.metadata.tables[table].primary_key.columns]
        if pk == ["id"]:
            out.add(table)
    return out
