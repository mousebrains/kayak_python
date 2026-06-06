"""Dataset layout descriptor — the single source of truth for a dataset's shape.

S4a introduces this so the validator, the fixture generator (and later
``init-dataset`` / ``generate-sources``) all agree on which files a dataset
must contain, the column set of each CSV, which tables carry stable ids, and
which columns live in the geometry/gradient JSON sidecars rather than in
``reach.csv``. S6 promotes this descriptor into the versioned contract
manifest; until then it is the contract.

CSV column sets are derived from the SQLAlchemy models minus the columns that
export to JSON sidecars or are runtime churn — exactly the rule
``scripts/export_metadata.py`` writes by — so the descriptor cannot drift from
the schema. Headers are validated as a **set** (the loaders key by column name;
column order is not semantically meaningful).
"""

from __future__ import annotations

from kayak.db.models import Base

# Columns present on the model but NOT written to the table's CSV: large
# machine-generated geometry/gradient (→ JSON sidecars) and runtime churn.
# Mirrors scripts/export_metadata.py::EXCLUDED_COLUMNS.
EXCLUDED_COLUMNS: dict[str, set[str]] = {
    "reach": {"geom", "gradient_profile"},
    "fetch_url": {"last_fetched_at"},
}

# Structurally required CSVs — the connected core a usable dataset always has.
# (FK integrity for the optional tables is enforced by the materialized load,
# so e.g. a source referencing a fetch_url forces fetch_url.csv to be present
# and complete via the foreign-key check rather than by listing it here.)
REQUIRED_CSVS: tuple[str, ...] = (
    "state",
    "source",
    "gauge",
    "gauge_source",
    "reach",
    "reach_state",
)
# Optional CSVs — known tables a minimal dataset may omit.
OPTIONAL_CSVS: tuple[str, ...] = (
    "fetch_url",
    "calc_expression",
    "reach_class",
    "class_description",
    "guidebook",
    "rating",
    "rating_data",
    "reach_guidebook",
    "huc_name",
)
KNOWN_CSVS: tuple[str, ...] = REQUIRED_CSVS + OPTIONAL_CSVS

# id_counters.csv is required and is itself not in KNOWN_CSVS (no id column).
ID_COUNTERS_CSV = "id_counters.csv"

# JSON sidecars keyed by reach id.
GEOM_JSON = "reaches.json"  # required: every reach must carry geometry
GRADIENT_JSON = "reaches-gradient.json"  # optional gradient profiles

# Child CSVs whose reach_id must reference an existing reach.
REACH_CHILD_CSVS: tuple[str, ...] = ("reach_state", "reach_class", "reach_guidebook")


def expected_columns(table: str) -> set[str]:
    """The exact CSV column-name set for ``table`` (model columns minus excluded)."""
    cols = {c.name for c in Base.metadata.tables[table].columns}
    return cols - EXCLUDED_COLUMNS.get(table, set())


def ordered_columns(table: str) -> list[str]:
    """CSV columns in model-definition order (for deterministic writers)."""
    excluded = EXCLUDED_COLUMNS.get(table, set())
    return [c.name for c in Base.metadata.tables[table].columns if c.name not in excluded]


def id_bearing_tables() -> set[str]:
    """Known tables whose primary key is a single ``id`` column — each must have
    exactly one ``id_counters`` entry."""
    out: set[str] = set()
    for table in KNOWN_CSVS:
        pk = [c.name for c in Base.metadata.tables[table].primary_key.columns]
        if pk == ["id"]:
            out.add(table)
    return out
