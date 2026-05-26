"""Guard: docs/database-schema.md documents every ORM column.

Round-3 review R2.2. The schema reference doc had drifted — four gauge
columns, source.timezone, and calc_expression.provenance_slug were present
in models.py but missing from the doc, and a prior review wrongly vouched it
"in lockstep." This test turns that from a review-only catch into a
merge-time gate: it parses the markdown column tables and asserts every
column in ``Base.metadata`` is documented.

Parser notes:
  * a table's columns are introduced by a ``### `name` `` heading OR a
    ``**`name`:**`` bold sub-label (the latter handles the combined
    "### `rating` and `rating_data`" section);
  * a single doc row may list more than one column (e.g. reach's
    ``| `latitude`, `longitude` | NUMERIC(9,6) | Midpoint |``), so every
    backticked identifier in the first cell counts.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from kayak.db.models import Base

_DOC = Path(__file__).resolve().parents[1] / "docs" / "database-schema.md"

# Tables exempt from the doc<->ORM checks, with a reason. schema_migrations is
# documented (the migration-bookkeeping table) but is raw DDL created by the
# migration runner, not an ORM model — so it's absent from Base.metadata and
# must be skipped in the reverse (doc -> ORM) direction. review-4 R2.4.
_IGNORE_TABLES: set[str] = {"schema_migrations"}

_HEADING = re.compile(r"^###\s+`([a-z0-9_]+)`")
_SUBLABEL = re.compile(r"^\*\*`([a-z0-9_]+)`:?\*\*")
_IDENT = re.compile(r"`([a-z0-9_]+)`")


def _documented_columns() -> dict[str, set[str]]:
    """Map each documented table name → the column names listed under it."""
    documented: dict[str, set[str]] = {}
    current: str | None = None
    in_fence = False
    for line in _DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _SUBLABEL.match(line) or _HEADING.match(line)
        if m:
            current = m.group(1)
            documented.setdefault(current, set())
            continue
        if current and line.startswith("| `"):
            first_cell = line.split("|")[1]  # between the 1st and 2nd pipe
            documented[current].update(_IDENT.findall(first_cell))
    return documented


def test_schema_doc_covers_every_model_column() -> None:
    documented = _documented_columns()
    missing: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name in _IGNORE_TABLES:
            continue
        doc_cols = documented.get(table.name)
        if doc_cols is None:
            missing.append(f"{table.name}: entire table undocumented")
            continue
        missing += [f"{table.name}.{c.name}" for c in table.columns if c.name not in doc_cols]
    assert not missing, (
        "docs/database-schema.md is out of sync with src/kayak/db/models.py.\n"
        "Undocumented (add them to the doc, or to _IGNORE_TABLES with a reason):\n  "
        + "\n  ".join(sorted(missing))
    )


def test_schema_doc_has_no_stale_columns() -> None:
    """Reverse direction (review-4 R2.4): every documented table + column must
    exist in the ORM, so a doc entry for a removed table/column fails too — the
    forward check above can't see that. schema_migrations is ignored (raw DDL)."""
    documented = _documented_columns()
    orm = {t.name: {c.name for c in t.columns} for t in Base.metadata.sorted_tables}
    stale: list[str] = []
    for table, doc_cols in documented.items():
        if table in _IGNORE_TABLES:
            continue
        orm_cols = orm.get(table)
        if orm_cols is None:
            stale.append(f"{table}: documented but not an ORM table")
            continue
        stale += [f"{table}.{c}" for c in sorted(doc_cols) if c not in orm_cols]
    assert not stale, (
        "docs/database-schema.md documents tables/columns absent from "
        "src/kayak/db/models.py (remove them, or add to _IGNORE_TABLES):\n  "
        + "\n  ".join(sorted(stale))
    )


def test_source_agency_enum_documents_every_value() -> None:
    """The source.agency Notes enum must list every distinct agency in the
    committed source.csv — the authoritative set (review-4 R2.4 / R3.4). The
    forward/reverse column checks only see column *names*, not enum prose."""
    csv_path = Path(__file__).resolve().parents[1] / "data" / "db" / "source.csv"
    with csv_path.open(encoding="utf-8") as fh:
        agencies = {
            (row.get("agency") or "").strip()
            for row in csv.DictReader(fh)
            if (row.get("agency") or "").strip()
        }
    notes = ""
    for line in _DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith("| `agency`"):
            cells = line.split("|")
            notes = cells[3] if len(cells) > 3 else ""
            break
    assert notes, "could not find the `agency` row in docs/database-schema.md"
    missing = sorted(a for a in agencies if a not in notes)
    assert not missing, (
        "docs/database-schema.md source.agency enum is missing values present "
        "in data/db/source.csv: " + ", ".join(missing)
    )
