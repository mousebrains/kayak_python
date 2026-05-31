"""Guard: the per-type id counters are valid monotonic high-water marks.

In the metadata-single-source model the numeric ``id`` is **stable and
author-assigned** (not a prod autoincrement), and ``base62(id)`` is the public
URL handle. ``data/db/id_counters.csv`` records the next id to assign per table;
it only ever increments, so a deleted id is never reused and a base-62 handle
never silently re-points to a different row. This guard keeps every table's ids
unique and strictly below its counter — catching a duplicate id (e.g. two
concurrent PRs both grabbing the same ``next_id``) or a stale counter at commit.

This catches an *immediate* collision (a new id ≤ the current max), but not a
counter that was *lowered* across history (deleting the top rows then dropping
``next_id`` to ``max+1`` would re-enable handle reuse). The full append-only
guarantee — ``next_id`` ≥ the previous commit's — wants a git-history check,
deferred to a later phase.
"""

from __future__ import annotations

import csv
from collections import Counter

from kayak.config import DATA_DIR

DB_DIR = DATA_DIR / "db"
COUNTERS = DB_DIR / "id_counters.csv"


def _counters() -> dict[str, int]:
    with COUNTERS.open(encoding="utf-8") as fh:
        return {row["table"]: int(row["next_id"]) for row in csv.DictReader(fh)}


def _ids(table: str) -> list[int]:
    with (DB_DIR / f"{table}.csv").open(encoding="utf-8") as fh:
        return [int(row["id"]) for row in csv.DictReader(fh) if (row.get("id") or "").strip()]


def test_every_counter_table_has_rows_with_ids() -> None:
    for table in _counters():
        assert _ids(table), f"{table}.csv (named in id_counters.csv) has no id rows"


def test_ids_are_unique_per_table() -> None:
    for table in _counters():
        dups = {i for i, c in Counter(_ids(table)).items() if c > 1}
        assert not dups, f"{table}.csv has duplicate ids: {sorted(dups)}"


def test_counter_is_above_every_existing_id() -> None:
    for table, nxt in _counters().items():
        hi = max(_ids(table))
        assert hi < nxt, f"{table}: next_id={nxt} ≤ max id {hi} — stale counter / id-reuse risk"
