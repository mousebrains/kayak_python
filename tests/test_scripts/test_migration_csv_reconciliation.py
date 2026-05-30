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
# Pending now: JDA/BON (0069, USACE Columbia dam outflow) and VAPW1/SHNO3
# (0070, NWS Vancouver/St. Helens stage).
PENDING_RECONCILIATION: set[str] = {}

# Across the whole class the form is `INSERT INTO source (name, ...) SELECT
# '<name>', ...` -- name is always the first column and the first SELECT literal.
_SOURCE_INSERT = re.compile(
    r"INSERT\s+INTO\s+source\s*\([^)]*\)\s*SELECT\s+'([^']*)'",
    re.IGNORECASE,
)

# The reconciliation guard above keys on the SELECT form. A future migration
# that wired a source via the VALUES form (`INSERT INTO source (...) VALUES
# (...)`) would slip past _SOURCE_INSERT entirely -- never extracted, so never
# checked against source.csv. This regex catches that alternate form so the
# convention test below can forbid it. All 32 current wiring INSERTs use SELECT.
_SOURCE_INSERT_VALUES = re.compile(
    r"INSERT\s+INTO\s+source\s*\([^)]*\)\s*VALUES",
    re.IGNORECASE,
)

# A drop-class migration that removes a source via the by-name form (`DELETE FROM
# source WHERE ... name = '<name>'`). Used to un-expect a previously-wired source
# from source.csv once a later migration deletes it -- see _deleted_sources().
_DELETE_SOURCE = re.compile(
    r"DELETE\s+FROM\s+source\s+WHERE[^;]*?\bname\s*=\s*'([^']*)'",
    re.IGNORECASE,
)


def _wired_sources() -> dict[str, str]:
    """Map each migration-wired source name to the first migration that wires it."""
    out: dict[str, str] = {}
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        for name in _SOURCE_INSERT.findall(path.read_text()):
            out.setdefault(name, path.name)
    return out


def _deleted_sources() -> set[str]:
    """Source names a (later) migration DELETEs by name.

    A source wired by an early migration's ``INSERT INTO source`` stays in
    ``_wired_sources()`` forever -- we never edit applied migrations -- so a later
    DROP-class migration that removes it (and removes its ``source.csv`` row)
    would otherwise read as reconciliation drift. Subtracting these keeps the
    guard honest for both adds and drops. Only the ``name = '...'`` delete form is
    recognized (the by-id form can't be mapped to a name without the DB), so a
    drop migration must delete the source by name to be seen here.
    """
    out: set[str] = set()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        out.update(_DELETE_SOURCE.findall(path.read_text()))
    return out


def _csv_source_names() -> set[str]:
    with SOURCE_CSV.open(encoding="utf-8") as fh:
        return {row["name"] for row in csv.DictReader(fh)}


def test_migration_wired_sources_are_in_source_csv() -> None:
    csv_names = _csv_source_names()
    deleted = _deleted_sources()
    offenders = {
        name: mig
        for name, mig in _wired_sources().items()
        if name not in csv_names and name not in PENDING_RECONCILIATION and name not in deleted
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


def test_no_source_insert_uses_values_form() -> None:
    # Keep the wire-via-migration convention on the SELECT form so the
    # reconciliation guard above (_SOURCE_INSERT) can't be bypassed: a
    # VALUES-form `INSERT INTO source (...) VALUES (...)` would never be
    # extracted, hence never reconciled against source.csv. All 32 current
    # source-inserts use SELECT, so this passes today; it fails the day a
    # migration introduces the VALUES form, prompting either a SELECT rewrite
    # or a deliberate broadening of the extractor.
    offenders = {
        path.name
        for path in sorted(MIGRATIONS_DIR.glob("*.sql"))
        if _SOURCE_INSERT_VALUES.search(path.read_text())
    }
    assert not offenders, (
        "migration(s) wire a source via `INSERT INTO source (...) VALUES (...)`, "
        "which the reconciliation guard's SELECT-form regex (_SOURCE_INSERT) "
        "does not capture -- rewrite as `INSERT INTO source (...) SELECT ...` so "
        f"the guard reconciles it against source.csv: {offenders}"
    )


def test_guard_sees_the_wire_via_migration_class() -> None:
    # Non-vacuity: the extractor must actually find wired sources (incl GPRO3),
    # so a broken regex can't make the guard silently pass on an empty set.
    wired = _wired_sources()
    assert "GPRO3" in wired, "extractor failed to find GPRO3 (0063)"
    assert len(wired) >= 5, f"expected the multi-migration wire class; got {sorted(wired)}"
