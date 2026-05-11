#!/usr/bin/env python3
"""Fix reach_class rows where 'flow' thresholds carry gage-height values.

Several reaches have low/high stored under low_data_type='flow' but the
numeric values (typically < 30) read like gage heights in feet rather
than CFS — the linked gauge usually publishes both, and the CFS value
for a runnable reach would be in the 100s. Propose flipping the
data_type from 'flow' to 'gauge' on each affected field.

Reaches with no gauge_id are skipped — without a gauge there's no
reading to compare against, so flipping the data_type doesn't help.

Default mode is dry-run; --apply walks per-row (y/n/q) and writes each
accepted update individually. A snapshot of kayak.db is taken before
any writes.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.engine.url import make_url

from kayak.config import DATABASE_URL
from kayak.db.engine import get_session
from kayak.db.models import LatestGaugeObservation, Reach, ReachClass


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--threshold",
        type=float,
        default=30.0,
        help="flow values below this are flagged as suspicious (default 30)",
    )
    p.add_argument("--reach-id", type=int, help="restrict to a single reach id")
    p.add_argument(
        "--apply",
        action="store_true",
        help="walk per-row review and write accepted updates (default: dry-run)",
    )
    args = p.parse_args()

    if args.apply:
        db_path = Path(make_url(DATABASE_URL).database)
        snap = db_path.with_name(f"{db_path.stem}.fix_data_type.{datetime.now():%Y%m%d_%H%M%S}.db")
        shutil.copy2(db_path, snap)
        print(f"Snapshot written: {snap}")

    with get_session(DATABASE_URL) as session:
        rows = session.execute(
            select(ReachClass, Reach)
            .join(Reach, Reach.id == ReachClass.reach_id)
            .where(
                Reach.gauge_id.is_not(None),
                (
                    (
                        (ReachClass.low_data_type == "flow")
                        & ReachClass.low.is_not(None)
                        & (ReachClass.low < args.threshold)
                    )
                    | (
                        (ReachClass.high_data_type == "flow")
                        & ReachClass.high.is_not(None)
                        & (ReachClass.high < args.threshold)
                    )
                ),
            )
            .order_by(Reach.id)
        ).all()

    if args.reach_id is not None:
        rows = [r for r in rows if r.Reach.id == args.reach_id]

    print(f"Suspicious reach_class rows: {len(rows)}")
    if not rows:
        return 0

    applied = skipped = 0
    for rc, reach in rows:
        with get_session(DATABASE_URL) as s2:
            avail_types = sorted(
                {
                    str(t)
                    for t in s2.execute(
                        select(LatestGaugeObservation.data_type).where(
                            LatestGaugeObservation.gauge_id == reach.gauge_id
                        )
                    )
                    .scalars()
                    .all()
                }
            )
        avail_str = ",".join(avail_types) or "(none)"
        display = reach.display_name or reach.name

        flip_low = rc.low_data_type == "flow" and rc.low is not None and rc.low < args.threshold
        flip_high = rc.high_data_type == "flow" and rc.high is not None and rc.high < args.threshold

        print()
        print(f"reach {reach.id}: {display!r} (class {rc.name}, rc_id={rc.id})")
        print(f"  current:  low={rc.low}/{rc.low_data_type}, high={rc.high}/{rc.high_data_type}")
        print(f"  gauge data types: {avail_str}")
        new_low_dt = "gauge" if flip_low else rc.low_data_type
        new_high_dt = "gauge" if flip_high else rc.high_data_type
        print(f"  proposed: low={rc.low}/{new_low_dt}, high={rc.high}/{new_high_dt}")
        if "gauge" not in avail_types:
            print(
                "  WARNING: linked gauge has no 'gauge' data type — "
                "flipping won't help unless the gauge starts publishing it."
            )

        if not args.apply:
            continue

        choice = input("  apply? [y]es / [n]o / [q]uit > ").strip().lower()
        if choice == "q":
            print("Stopping.")
            break
        if choice == "y":
            values = {}
            if flip_low:
                values["low_data_type"] = "gauge"
            if flip_high:
                values["high_data_type"] = "gauge"
            if values:
                with get_session(DATABASE_URL) as s2:
                    s2.execute(update(ReachClass).where(ReachClass.id == rc.id).values(**values))
                    s2.commit()
                applied += 1
        else:
            skipped += 1

    mode = "DRY RUN" if not args.apply else "APPLY"
    print(f"\n{mode} done: applied={applied} skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
