"""Guard: every reach in the committed snapshot has a unique, non-empty name.

`reach.name` is the symbolic foreign key (and the stable public handle) in the
metadata-single-source redesign, so a reach without a name — or two reaches
sharing one — would break a symbolic-FK CSV load and the hash/name lookups.
The 34 once-nameless reaches were named in migration 0073 (Phase 1b); this
keeps them that way and catches the next nameless/duplicate reach at commit.
"""

from __future__ import annotations

import csv
from collections import Counter

from kayak.config import METADATA_DIR

REACH_CSV = METADATA_DIR / "reach.csv"


def _reach_names() -> list[str]:
    with REACH_CSV.open(encoding="utf-8") as fh:
        return [(row.get("name") or "").strip() for row in csv.DictReader(fh)]


def test_every_reach_has_a_name() -> None:
    names = _reach_names()
    missing = sum(1 for n in names if not n)
    assert missing == 0, f"{missing} reach(es) in reach.csv have an empty name"


def test_reach_names_are_unique() -> None:
    names = [n for n in _reach_names() if n]
    dups = {n: c for n, c in Counter(names).items() if c > 1}
    assert not dups, f"duplicate reach.name values in reach.csv: {dups}"
