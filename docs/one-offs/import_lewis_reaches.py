#!/usr/bin/env python3
"""Stage + NHD-trace the WA Lewis-system reaches (Batch B) into a dev DB for
the coordinate-refine loop. Adapted from import_mt_reaches.py.

Run with the brew python that has osgeo (NOT .venv):

    /opt/homebrew/opt/python@3.14/bin/python3.14 \\
        docs/one-offs/import_lewis_reaches.py --db /tmp/sandbox_batchB.db --trace

Wires the two new gauges (NF Lewis 14216000, Canyon Creek 14219000; EF Lewis
14222500 already exists as gauge 53), inserts 12 reach rows + reach_state +
reach_class, then traces each to set reach.geom + length.

Where the user has supplied refined endpoints in REFINED_COORDS, those
override the raw AW-cache lat/lon during phase1_stage (so a fresh re-run
reproduces the post-refine state); aw_ids absent from REFINED_COORDS fall
back to the raw AW values, which is the entry point for the description.php
refine loop on newly-added reaches. Re-run with `--retrace <aw_id ...>`
after editing endpoints to recompute geom + length.

This is a staging aid only -- the committed artifact is migration 0067 +
reaches.json + reaches-gradient.json, written after coords are refined.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

CACHE = Path(__file__).resolve().parents[2] / "Gauge-metadata-cache" / "gauges.db"

# aw_id -> usgs_id of the gauge it rides (section order; final sort is by put-in elevation)
AW_TO_USGS: dict[int, str] = {
    3531: "14216000",
    3495: "14216000",
    5711: "14216000",
    2151: "14216000",
    2152: "14216000",
    2149: "14222500",
    2147: "14222500",
    2150: "14222500",
    2148: "14222500",
    3530: "14222500",
    2073: "14219000",
    3066: "14219000",
}

# usgs_id -> (gauge.name, lat, lon, river, state, huc8, display_name) for gauges to create.
# 14222500 (EF Lewis) already exists, so it is not listed here.
NEW_GAUGES: dict[str, tuple[str, float, float, str, str, str, str]] = {
    "14216000": (
        "14216000",
        46.060355,
        -121.9845094,
        "Lewis",
        "WA",
        "17080002",
        "Lewis above Muddy River",
    ),
    "14219000": (
        "14219000",
        45.9398339,
        -122.3170398,
        "Canyon Creek",
        "WA",
        "17080002",
        "Canyon Creek near Amboy",
    ),
}

# User-refined put-in/take-out endpoints (collected 2026-05-27 via the
# description.php right-click latlon tool) keyed by aw_id, each as
# (pi_lat, pi_lon, to_lat, to_lon). Where present these supersede the raw
# AW cache coords during phase1_stage. Sections are stitched contiguous on
# each branch (take-out of one = put-in of the next, modulo intentional
# portage/whitewater gaps). aw_2073 put-in was moved off a Canyon Creek
# side-creek snap onto the main stem (the only correction past the initial
# refine pass; everything else was right on first try).
REFINED_COORDS: dict[int, tuple[float, float, float, float]] = {
    # NF Lewis (gauge 14216000)
    3531: (46.21486094346499, -121.66801578410308, 46.19640959341199, -121.72925618344003),
    3495: (46.19640959341199, -121.72925618344003, 46.17945325489507, -121.84676665051347),
    5711: (46.17945325489507, -121.84676665051347, 46.14437244684149, -121.89553866310538),
    2151: (46.14437244684149, -121.89553866310538, 46.06209974912408, -121.9666341723693),
    2152: (46.06209974912408, -121.9666341723693, 46.06564227989721, -122.02014216013376),
    # EF Lewis (gauge 14222500)
    2149: (45.82291866281684, -122.16455790254149, 45.81764938991045, -122.25274251100815),
    2147: (45.81764938991045, -122.25274251100815, 45.81454377126033, -122.32450146604936),
    2150: (45.814750059931576, -122.36795749513439, 45.83150650515193, -122.39008184333217),
    2148: (45.831361463316455, -122.38912294784332, 45.82275202437011, -122.53399713660596),
    3530: (45.82275202437011, -122.53399713660596, 45.81462834437051, -122.58909912953601),
    # Canyon Creek (gauge 14219000)
    2073: (45.90745071616666, -122.18612869486408, 45.940055928208714, -122.31642110987625),
    3066: (45.940055928208714, -122.31642110987625, 45.96060252165227, -122.37251988537328),
}


def _float(v: object) -> float | None:
    try:
        return float(v) if v not in (None, "") else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def load_meta(meta_db: sqlite3.Connection, aw_id: int) -> dict | None:
    row = meta_db.execute(
        """SELECT id, river, section, class, state, put_in_lat, put_in_lon,
                  take_out_lat, take_out_lon, length, avg_gradient, max_gradient,
                  begin_low_runnable, end_high_runnable
           FROM aw_reach WHERE id = ?""",
        (aw_id,),
    ).fetchone()
    if not row:
        return None
    return {
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
        "rmin": _float(row[12]),
        "rmax": _float(row[13]),
    }


def wire_gauges(db: sqlite3.Connection) -> None:
    """Create the 2 new USGS gauges (idempotent): gauge + USGS source + link."""
    for usgs_id, (name, lat, lon, river, state, huc, disp) in NEW_GAUGES.items():
        if db.execute("SELECT 1 FROM gauge WHERE usgs_id = ?", (usgs_id,)).fetchone():
            print(f"gauge {usgs_id}: exists")
            continue
        db.execute(
            """INSERT INTO gauge (name, usgs_id, latitude, longitude, river, location,
                                  display_name, state, huc, allow_negative_flow)
               VALUES (?,?,?,?,?,?,?,?,?,0)""",
            (name, usgs_id, lat, lon, river, disp, f"{river} at {disp}", state, huc),
        )
        if not db.execute(
            "SELECT 1 FROM source WHERE name=? AND agency='USGS'", (usgs_id,)
        ).fetchone():
            db.execute("INSERT INTO source (name, agency) VALUES (?, 'USGS')", (usgs_id,))
        gid = db.execute("SELECT id FROM gauge WHERE name=?", (name,)).fetchone()[0]
        sid = db.execute(
            "SELECT id FROM source WHERE name=? AND agency='USGS'", (usgs_id,)
        ).fetchone()[0]
        db.execute(
            "INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) VALUES (?,?)", (gid, sid)
        )
        print(f"gauge {usgs_id}: wired (gauge {gid}, source {sid})")
    db.commit()


def phase1_stage(db: sqlite3.Connection, meta_db: sqlite3.Connection) -> None:
    """Insert the 12 reach rows (no geom) + reach_state + reach_class."""
    wa_state_id = db.execute("SELECT id FROM state WHERE abbreviation = 'WA'").fetchone()
    if not wa_state_id:
        sys.exit("error: no WA row in state table")
    wa_state_id = wa_state_id[0]
    existing = {r[0] for r in db.execute("SELECT aw_id FROM reach WHERE aw_id IS NOT NULL")}

    for aw_id, usgs_id in AW_TO_USGS.items():
        if aw_id in existing:
            print(f"reach aw_{aw_id}: exists")
            continue
        meta = load_meta(meta_db, aw_id)
        if not meta:
            print(f"WARN aw_{aw_id}: not in cache")
            continue
        g = db.execute("SELECT id FROM gauge WHERE usgs_id = ?", (usgs_id,)).fetchone()
        if not g:
            print(f"WARN aw_{aw_id}: gauge {usgs_id} not found")
            continue
        gid = g[0]
        river, section = meta["river"] or "", meta["section"] or ""
        plat, plon, tlat, tlon = meta["plat"], meta["plon"], meta["tlat"], meta["tlon"]
        if aw_id in REFINED_COORDS:
            plat, plon, tlat, tlon = REFINED_COORDS[aw_id]
        lat = (plat + tlat) / 2 if (plat and tlat) else (plat or tlat)
        lon = (plon + tlon) / 2 if (plon and tlon) else (plon or tlon)
        cur = db.execute(
            """INSERT INTO reach
               (name, display_name, sort_name, river, gauge_id, description, difficulties,
                length, gradient, max_gradient, latitude_start, longitude_start,
                latitude_end, longitude_end, latitude, longitude, aw_id, no_show)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
            (
                f"aw_{aw_id}",
                river,
                f"{_slug(river)} {aw_id:05d}",
                river,
                gid,
                section or None,
                meta["class"],
                meta["length"],
                meta["avggradient"],
                meta["maxgradient"],
                plat,
                plon,
                tlat,
                tlon,
                lat,
                lon,
                aw_id,
            ),
        )
        rid = cur.lastrowid
        db.execute(
            "INSERT OR IGNORE INTO reach_state (reach_id, state_id) VALUES (?,?)",
            (rid, wa_state_id),
        )
        if meta["class"]:
            db.execute(
                "INSERT INTO reach_class (reach_id, name, low, low_data_type, high, high_data_type) "
                "VALUES (?,?,?,'flow',?,'flow')",
                (rid, meta["class"], meta["rmin"], meta["rmax"]),
            )
        print(f"reach aw_{aw_id}: staged (reach {rid}, {river} - {section[:40]})")
    db.commit()


def trace(db: sqlite3.Connection, only: list[int] | None) -> None:
    """NHD-trace each staged reach lacking a geom (or in `only`); set geom + length."""
    from kayak.tracing import trace as impl

    ids = only if only else list(AW_TO_USGS)
    rows = db.execute(
        "SELECT id, aw_id, river, latitude_start, longitude_start, latitude_end, "
        "longitude_end, geom FROM reach WHERE aw_id IN ({})".format(",".join("?" * len(ids))),
        ids,
    ).fetchall()
    for rid, aw_id, river, plat, plon, tlat, tlon, geom in rows:
        if geom and not only:
            print(f"aw_{aw_id}: geom present, skip")
            continue
        if not all((plat, plon, tlat, tlon)):
            print(f"aw_{aw_id}: missing endpoint")
            continue
        try:
            coords = impl.trace_reach(
                (float(plat), float(plon)), (float(tlat), float(tlon)), verbose=False
            )
        except Exception as exc:
            print(f"aw_{aw_id}: trace FAILED: {exc}")
            continue
        if not coords:
            print(f"aw_{aw_id}: empty trace")
            continue
        miles = float(impl.total_distance(coords))
        geom_str = ",".join(f"{lon:.6f} {lat:.6f}" for (lat, lon) in coords)
        db.execute("UPDATE reach SET geom=?, length=? WHERE id=?", (geom_str, miles, rid))
        print(f"aw_{aw_id} ({river}): {len(coords)} vertices, {miles:.2f} mi")
    db.commit()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="dev SQLite DB to stage into")
    ap.add_argument("--trace", action="store_true", help="trace after staging")
    ap.add_argument(
        "--retrace", type=int, nargs="*", help="re-trace these aw_ids (after coord edits)"
    )
    args = ap.parse_args()

    db = sqlite3.connect(args.db)
    meta_db = sqlite3.connect(f"file:{CACHE}?mode=ro", uri=True)
    if args.retrace is not None:
        # clear geoms so trace() recomputes
        db.executemany("UPDATE reach SET geom=NULL WHERE aw_id=?", [(a,) for a in args.retrace])
        db.commit()
        trace(db, args.retrace)
        return 0
    wire_gauges(db)
    phase1_stage(db, meta_db)
    if args.trace:
        trace(db, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
