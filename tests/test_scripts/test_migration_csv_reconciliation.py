"""Guard: every source a migration wires must be reconciled into source.csv.

Some migrations (the 0027/0063 "wire-via-migration" class) ``INSERT INTO source``
directly on prod to wire a live feed onto an existing gauge -- e.g. 0063 added
GPRO3 (NWRFC) to Green Peter when the USACE source froze. Those rows then have to
be reconciled into the committed ``data/db/source.csv`` snapshot
(``scripts/export_metadata.py``) so a fresh ``init-db`` + ``import_metadata``
reproduces them. A wired source missing from source.csv means the snapshot
drifted -- the exact gap that left GPRO3 in the DB but not the CSVs until the
nightly snapshot reconciled it (review-4 R4.4). This guard catches that drift
going forward.

``PENDING_RECONCILIATION`` lists wired sources not yet in source.csv because the
reconciliation is a prod ``export_metadata.py`` snapshot (their ids are
prod-assigned autoincrements, so they can't be hand-added). Remove an entry once
it lands in source.csv -- the stale-allowlist test below fails until you do.
"""

from __future__ import annotations

import csv
import re

from kayak.config import DATA_DIR

MIGRATIONS_DIR = DATA_DIR / "db" / "migrations"
SOURCE_CSV = DATA_DIR / "db" / "source.csv"

# Sources wired by a migration but not yet in source.csv (their ids are
# prod-assigned, so reconciliation is a prod export_metadata.py snapshot). Add a
# name here ONLY while its snapshot is pending; the stale-allowlist test forces
# removal once it lands. (GPRO3 from 0063 was reconciled by snapshot e408fa8; the
# 0065 USGS split + the Batch A/B/C gauges from 0066-0068 by snapshot 8ce7366.)
PENDING_RECONCILIATION: set[str] = set()

# Across the whole class the form is `INSERT INTO source (name, ...) SELECT
# '<name>', ...` -- name is always the first column and the first SELECT literal.
_SOURCE_INSERT = re.compile(
    r"INSERT\s+INTO\s+source\s*\([^)]*\)\s*SELECT\s+'([^']*)'",
    re.IGNORECASE,
)


def _wired_sources() -> dict[str, str]:
    """Map each migration-wired source name to the first migration that wires it."""
    out: dict[str, str] = {}
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        for name in _SOURCE_INSERT.findall(path.read_text()):
            out.setdefault(name, path.name)
    return out


def _csv_source_names() -> set[str]:
    with SOURCE_CSV.open(encoding="utf-8") as fh:
        return {row["name"] for row in csv.DictReader(fh)}


def test_migration_wired_sources_are_in_source_csv() -> None:
    csv_names = _csv_source_names()
    offenders = {
        name: mig
        for name, mig in _wired_sources().items()
        if name not in csv_names and name not in PENDING_RECONCILIATION
    }
    assert not offenders, (
        "source(s) wired by a migration but missing from data/db/source.csv -- run "
        "scripts/export_metadata.py on prod and commit the snapshot, or add to "
        f"PENDING_RECONCILIATION if a snapshot is pending: {offenders}"
    )


def test_pending_reconciliation_allowlist_is_not_stale() -> None:
    # Once a pending source lands in source.csv, drop it from the allowlist so the
    # guard resumes enforcing it (keeps the exception set shrinking, not permanent).
    reconciled = _csv_source_names() & PENDING_RECONCILIATION
    assert not reconciled, (
        "these PENDING_RECONCILIATION sources are now in source.csv -- remove them "
        f"from the allowlist: {reconciled}"
    )


def test_guard_sees_the_wire_via_migration_class() -> None:
    # Non-vacuity: the extractor must actually find wired sources (incl GPRO3),
    # so a broken regex can't make the guard silently pass on an empty set.
    wired = _wired_sources()
    assert "GPRO3" in wired, "extractor failed to find GPRO3 (0063)"
    assert len(wired) >= 5, f"expected the multi-migration wire class; got {sorted(wired)}"
