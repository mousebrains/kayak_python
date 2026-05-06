#!/usr/bin/env python3
"""Give every gauge with a usgs_id a dedicated USGS source named with its
station digits, so fetch-usgs-ogc stops dumping USGS observations into NWS
(or USACE / USBR) source rows.

Background: src/kayak/cli/fetch_usgs_ogc.py:_build_site_map prefers a
source whose name == gauge.usgs_id. If none exists it falls back to any
linked source — typically the NWS one — and the two feeds collide on the
same (source_id, observed_at, data_type) primary key. The result is a
visible 15-min "wiggle" between two disagreeing rating curves on the
flow plot.

Three actions per gauge:

  SKIP    — already has a source named with its usgs_id (correct state).
  RENAME  — has a USGS-flavored source under a different name (e.g.
            ELK_CREEK_NR_TRAIL for usgs_id 14338000). Rename it.
  INSERT  — no USGS-style source linked at all. Create one and link it.

The NWS source is always left in place — keeping it preserves redundancy
and gives `levels merge` something to fuse later if the overlay on the
PHP plot becomes a nuisance.

Usage:
    /home/pat/.venv/bin/python3 scripts/split_usgs_sources.py
    /home/pat/.venv/bin/python3 scripts/split_usgs_sources.py --apply
    /home/pat/.venv/bin/python3 scripts/split_usgs_sources.py --gauge-id 140 --apply
"""

import argparse
import sys
from pathlib import Path

# Repo src/ on path so we can import kayak.* without an editable install
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select

from kayak.db.engine import get_session
from kayak.db.models import Gauge, GaugeSource, Source


def classify(session, gauge):
    """Return (action, source_id_or_none, detail) for one gauge.

    action ∈ {"SKIP", "RENAME", "INSERT"}.
    For SKIP / RENAME, source_id is the source we'll keep/rename.
    For INSERT, source_id is None (caller assigns after insert).
    """
    rows = session.execute(
        select(Source.id, Source.name, Source.agency, Source.fetch_url_id)
        .join(GaugeSource, GaugeSource.source_id == Source.id)
        .where(GaugeSource.gauge_id == gauge.id)
    ).all()

    for sid, name, _agency, _fu in rows:
        if name == gauge.usgs_id:
            return ("SKIP", sid, f"already named {name!r}")

    # Look for a USGS-flavored source: no fetch_url (so OGC is the only
    # writer) and agency mentions USGS. This covers single-agency 'USGS'
    # rows and combos like 'USGS USACE'.
    candidates = [
        (sid, name, agency)
        for sid, name, agency, fu in rows
        if fu is None and agency and "USGS" in agency
    ]
    if candidates:
        # Pick the lowest source_id for a deterministic choice when more
        # than one fits (rare).
        candidates.sort()
        sid, name, agency = candidates[0]
        return ("RENAME", sid, f"{name!r} (agency={agency!r}) -> {gauge.usgs_id!r}")

    return ("INSERT", None, f"no USGS source — will create name={gauge.usgs_id!r}")


def apply_action(session, gauge, action, source_id):
    """Execute the action. Caller decides whether to commit."""
    if action == "SKIP":
        return

    if action == "RENAME":
        src = session.get(Source, source_id)
        src.name = gauge.usgs_id
        return

    if action == "INSERT":
        new_src = Source(
            name=gauge.usgs_id,
            agency="USGS",
            fetch_url_id=None,
            calc_expression_id=None,
            timezone=None,
        )
        session.add(new_src)
        session.flush()  # get id
        session.add(GaugeSource(gauge_id=gauge.id, source_id=new_src.id))
        return

    raise ValueError(f"unknown action: {action!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Commit changes. Without this flag the script only prints the plan.",
    )
    ap.add_argument(
        "--gauge-id",
        type=int,
        default=None,
        help="Limit to a single gauge id (for the pilot run).",
    )
    args = ap.parse_args()

    session = get_session()
    try:
        stmt = select(Gauge).where(Gauge.usgs_id.is_not(None)).order_by(Gauge.id)
        if args.gauge_id is not None:
            stmt = stmt.where(Gauge.id == args.gauge_id)
        gauges = list(session.scalars(stmt))

        if not gauges:
            print("No gauges matched.")
            return 0

        counts = {"SKIP": 0, "RENAME": 0, "INSERT": 0}
        hdr = f"{'gauge_id':>8}  {'usgs_id':<10}  {'name':<32}  {'action':<7}  detail"
        print(hdr)
        print("-" * len(hdr))

        for g in gauges:
            action, source_id, detail = classify(session, g)
            counts[action] += 1
            print(
                f"{g.id:>8}  {g.usgs_id or '':<10}  {(g.name or '')[:32]:<32}  {action:<7}  {detail}"
            )
            if args.apply:
                apply_action(session, g, action, source_id)

        print()
        total = sum(counts.values())
        print(
            f"Totals: SKIP={counts['SKIP']}  RENAME={counts['RENAME']}  INSERT={counts['INSERT']}  (of {total})"
        )

        if args.apply:
            session.commit()
            print("Committed.")
        else:
            print("Dry run — pass --apply to commit.")

        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
