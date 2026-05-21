#!/usr/bin/env python3
"""Import the 16 curated Montana AW reaches paired with the mt.list gauges.

11 cache-recorded (AW's canonical USGS gauge is in our mt.list) + 5 paddler-
curated proxy (AW's canonical gauge is **not** in our list, but Pat has
nominated a nearby in-list gauge as a usable proxy until MT boaters
verify). See docs/PLAN_montana_gauges.md § "Reach pairings".

Three phases:
  1. Insert 16 reach rows from Gauge-metadata-cache/gauges.db::aw_reach
     with explicit aw_id→gauge_id mapping (no geom yet).
  2. NHD HR trace each reach (put-in→take-out along flowlines), populate
     reach.geom + refresh reach.length from the actual flowline distance.
  3. (Caller runs separately) refresh_reach_elevations.py + levels assign-huc
     + levels build.

Usage:
    python3 docs/one-offs/import_mt_reaches.py --insert --dry-run
    python3 docs/one-offs/import_mt_reaches.py --insert
    python3 docs/one-offs/import_mt_reaches.py --trace
    python3 docs/one-offs/import_mt_reaches.py --insert --trace      # combined
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys

DEFAULT_DB = os.environ.get("KAYAK_DB", "/home/pat/DB/kayak.db")
DEFAULT_META_DB = "/home/pat/kayak/Gauge-metadata-cache/gauges.db"

# AW reach ID → kayak gauge.id. For "cache-recorded" rows the gauge is also
# AW's canonical reading. For "proxy" rows AW's canonical gauge is *not*
# in our DB; we nominate the listed in-list gauge as a usable proxy.
AW_TO_GAUGE: dict[int, tuple[int, str]] = {
    # Cache-recorded
    981: (197, "cache-recorded"),  # Belt Creek 06090500
    984: (200, "cache-recorded"),  # Blackfoot 12340000
    995: (201, "cache-recorded"),  # Clark Fork 12354500
    996: (201, "cache-recorded"),
    998: (202, "cache-recorded"),  # Dearborn 06073500
    1005: (203, "cache-recorded"),  # SF Flathead 12359800
    1021: (207, "cache-recorded"),  # Smith 06077200
    3778: (203, "cache-recorded"),
    4358: (202, "cache-recorded"),
    4359: (202, "cache-recorded"),
    10916: (203, "cache-recorded"),
    # Proxy (pending MT boater verification)
    983: (199, "proxy"),  # Big Hole "Dewey to Divide" → 06025250 Maiden Rock
    1012: (205, "proxy"),  # Madison "Beartrap Canyon"  → 06038800 Kirby Ranch
    1013: (205, "proxy"),  # Madison "Quake Lake"       → 06038800 Kirby Ranch
    3227: (206, "proxy"),  # Missouri "Great Falls"     → 06066500 Holter Dam
    10730: (209, "proxy"),  # Sun "SF Wilderness"        → 06085800 Simms
}


def _float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _slug(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


def load_meta(meta_db: sqlite3.Connection, aw_id: int) -> dict | None:
    row = meta_db.execute(
        """SELECT id, river, section, class, state,
                  put_in_lat, put_in_lon, take_out_lat, take_out_lon,
                  length, avg_gradient, max_gradient, gauges,
                  begin_low_runnable, end_high_runnable
           FROM aw_reach WHERE id = ?""",
        (aw_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "river": row[1],
        "section": row[2],
        "class": row[3],
        "state": row[4],
        "plat": _float(row[5]),
        "plon": _float(row[6]),
        "tlat": _float(row[7]),
        "tlon": _float(row[8]),
        "length": _float(row[9]),
        "avggradient": _float(row[10]),
        "maxgradient": _float(row[11]),
        "gauges": json.loads(row[12]) if row[12] else [],
        "rmin": _float(row[13]),
        "rmax": _float(row[14]),
    }


def phase1_insert(
    db: sqlite3.Connection, meta_db: sqlite3.Connection, dry_run: bool
) -> list[tuple[int, int]]:
    """Insert 16 reach rows (no geom). Returns [(reach_id, aw_id), ...]."""
    state_map = {
        row[1].upper(): row[0]
        for row in db.execute("SELECT id, abbreviation FROM state WHERE abbreviation IS NOT NULL")
    }
    mt_state_id = state_map.get("MT")
    if not mt_state_id:
        print("error: no MT row in state table", file=sys.stderr)
        sys.exit(2)

    existing = {row[0] for row in db.execute("SELECT aw_id FROM reach WHERE aw_id IS NOT NULL")}
    created: list[tuple[int, int]] = []

    for aw_id, (gauge_id, kind) in AW_TO_GAUGE.items():
        if aw_id in existing:
            print(f"SKIP aw_id={aw_id}: already in reach table")
            continue
        meta = load_meta(meta_db, aw_id)
        if not meta:
            print(f"WARN aw_id={aw_id}: not in aw_reach cache")
            continue

        river = meta["river"] or ""
        section = meta["section"] or ""
        display_name = river
        name = f"aw_{aw_id}"
        sort_name = f"{_slug(river)} {aw_id:05d}"
        plat, plon = meta["plat"], meta["plon"]
        tlat, tlon = meta["tlat"], meta["tlon"]
        lat = ((plat + tlat) / 2.0) if (plat and tlat) else (plat or tlat)
        lon = ((plon + tlon) / 2.0) if (plon and tlon) else (plon or tlon)
        difficulties = meta["class"]
        length = meta["length"]
        gradient = meta["avggradient"]
        max_gradient = meta["maxgradient"]

        tag = "[DRY-RUN]" if dry_run else "INSERT"
        print(f"{tag} aw_{aw_id} ({kind:<14}) gauge={gauge_id}  {river} — {section[:50]}")

        if dry_run:
            continue

        cur = db.execute(
            """INSERT INTO reach
               (name, display_name, sort_name, river, gauge_id,
                description, difficulties, length, gradient, max_gradient,
                latitude_start, longitude_start, latitude_end, longitude_end,
                latitude, longitude, aw_id, no_show)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                name,
                display_name,
                sort_name,
                river,
                gauge_id,
                section or None,
                difficulties,
                length,
                gradient,
                max_gradient,
                plat,
                plon,
                tlat,
                tlon,
                lat,
                lon,
                aw_id,
            ),
        )
        reach_id = cur.lastrowid
        db.execute(
            "INSERT OR IGNORE INTO reach_state (reach_id, state_id) VALUES (?, ?)",
            (reach_id, mt_state_id),
        )
        if (meta["rmin"] is not None or meta["rmax"] is not None) and difficulties:
            db.execute(
                "INSERT INTO reach_class (reach_id, name, low, low_data_type, high, high_data_type) "
                "VALUES (?, ?, ?, 'flow', ?, 'flow')",
                (reach_id, difficulties, meta["rmin"], meta["rmax"]),
            )
        created.append((reach_id, aw_id))

    if not dry_run:
        db.commit()
    print(f"\nPhase 1: {len(created)} reach(es) inserted.")
    return created


def phase2_trace(db: sqlite3.Connection, dry_run: bool) -> None:
    """NHD HR trace each MT reach, set reach.geom + recompute reach.length."""
    from kayak.tracing import trace as impl  # heavy import here

    rows = db.execute(
        """SELECT id, name, aw_id, river, latitude_start, longitude_start,
                  latitude_end, longitude_end, length, geom
           FROM reach
           WHERE aw_id IN ({})
           ORDER BY aw_id""".format(",".join(str(k) for k in AW_TO_GAUGE))
    ).fetchall()

    print(f"\nPhase 2: trace {len(rows)} MT reach(es)…\n")
    updates: list[tuple[str, float, int]] = []

    for row in rows:
        reach_id, name, aw_id, river, plat, plon, tlat, tlon, old_len, old_geom = row
        if old_geom:
            print(f"SKIP {name} ({river}): geom already populated ({len(old_geom)} chars)")
            continue
        if not all((plat, plon, tlat, tlon)):
            print(f"WARN {name}: missing put-in or take-out coords")
            continue

        print(f"--- {name}  {river}")
        try:
            coords = impl.trace_reach(
                (float(plat), float(plon)), (float(tlat), float(tlon)), verbose=False
            )
        except Exception as exc:
            print(f"  FAIL trace: {exc}")
            continue

        if not coords:
            print("  FAIL: empty trace")
            continue

        miles = float(impl.total_distance(coords))
        geom = ",".join(f"{lon:.6f} {lat:.6f}" for (lat, lon) in coords)
        print(f"  {len(coords)} vertices, {miles:.2f} mi (was {old_len})")

        if dry_run:
            continue
        updates.append((geom, miles, reach_id))

    if dry_run:
        print("\n(dry-run; no DB changes)")
        return

    db.executemany(
        "UPDATE reach SET geom = ?, length = ? WHERE id = ?",
        updates,
    )
    db.commit()
    print(f"\nPhase 2: {len(updates)} reach(es) updated with geom + length.")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--metadata-db", default=DEFAULT_META_DB)
    p.add_argument("--insert", action="store_true", help="Run Phase 1: insert reach rows")
    p.add_argument("--trace", action="store_true", help="Run Phase 2: NHD HR trace")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if not (args.insert or args.trace):
        p.error("specify at least one of --insert / --trace")

    db = sqlite3.connect(args.db)
    meta_db = sqlite3.connect(args.metadata_db)

    if args.insert:
        phase1_insert(db, meta_db, args.dry_run)
    if args.trace:
        phase2_trace(db, args.dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main())
