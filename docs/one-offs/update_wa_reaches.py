#!/usr/bin/env python3
"""Apply Pat's review corrections to the 11 SW-Washington reaches (wa.notes).

Take-3 addendum: the Tilton-family Bennett refs (430: p91/r65, 431: p93/r67,
432: p92/r66) were supplied after this script first ran and are included in
the table below for provenance; this script is the complete metadata record
(endpoints, descriptions, guidebook refs, sort keys); the trace/gradient
steps (re-trace, 3DEP refresh, sample+compute gradient profiles) ran via
the tools named in import_wa_lower_columbia.py's Reproduce block.

Per reach: corrected put-in/take-out coordinates, cleaned description text,
Bennett guidebook (id 6) page/run references, and a re-trace from the new
endpoints — NHD HR by default, the OSM main-channel tracer (NHD-gated) for
the three serpentine sections the NHD trace mangled (Tilton x2, NF Toutle).
Reach 428 (SF Toutle) provenance: an interim revision flagged it
gradient_unreliable because AW's published max_gradient (68 ft/mi) was
impossible against the 3DEP profile; the flag was cleared once lidar-grade
3DEP verified the elevations and compute_reach_gradient.py replaced AW's
figure (steepest mile 358, avg 120) — the reviewer accepted the NHD trace
(braided at low flow; the whole channel fills at paddleable flows).
Green's reach display name becomes plain "Green"; NF Tilton's sort key moves
it ahead of the Tilton runs ("Tilton NF 0" < "Tilton ag 01" byte-wise, the
"Smith NF 0" precedent).

Run under brew python (osgeo stack):
    PYTHONPATH=src /opt/homebrew/bin/python3.13 docs/one-offs/update_wa_reaches.py
Then re-run the elevation / HUC / export steps listed in
docs/one-offs/import_wa_lower_columbia.py's Reproduce block.
"""

from __future__ import annotations

import itertools
import os
import sqlite3
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from kayak.tracing import osm as osm_impl
from kayak.tracing import trace as impl

DB = os.environ.get("KAYAK_DB", "/Users/pat/tpw/DB/kayak.db")
BENNETT_WA = 6  # A Guide to the Whitewater Rivers of Washington, 2nd ed.

# reach_id: (putin_lat, putin_lon, takeout_lat, takeout_lon, description,
#            osm_river_or_None, guidebook_(page, run)_or_None)
REACHES = {
    422: (
        46.110297,
        -122.358631,
        46.048444,
        -122.609629,
        "Upper Falls to Gobar Creek",
        None,
        (60, 33),
    ),
    423: (
        46.048444,
        -122.609629,
        46.016143,
        -122.734301,
        "Gobar Creek to Hatchery",
        None,
        (61, 34),
    ),
    424: (
        46.020194,
        -122.731946,
        46.047448,
        -122.837330,
        "Lower Falls to Red Barn",
        None,
        (61, 35),
    ),
    425: (46.150784, -122.586114, 46.171112, -122.731994, None, None, (62, 36)),
    426: (46.329339, -122.725021, 46.334427, -122.840409, "Hwy 504 to Tower Rd", None, (67, 41)),
    427: (
        46.372720,
        -122.577596,
        46.329339,
        -122.725010,
        "Green River to SF confluence",
        "North Fork Toutle",
        (65, 39),
    ),
    428: (46.209299, -122.270665, 46.252338, -122.576641, None, None, (66, 40)),
    429: (46.389320, -122.352161, 46.376629, -122.528039, None, None, (62, 37)),
    430: (46.559457, -122.288547, 46.594602, -122.443990, "Morton to Bremer", "Tilton", (91, 65)),
    431: (
        46.580121,
        -122.416867,
        46.562103,
        -122.537878,
        "Bremer to Ike Kinswa",
        "Tilton",
        (93, 67),
    ),
    432: (46.619812, -122.388875, 46.597573, -122.365175, "abv Tilton confluence", None, (92, 66)),
}


def arc_length_midpoint(coords_latlon):
    """(lat, lon) at 50% cumulative length."""
    if len(coords_latlon) < 2:
        return None
    seg = [impl.haversine(a[0], a[1], b[0], b[1]) for a, b in itertools.pairwise(coords_latlon)]
    total = sum(seg)
    if total == 0:
        return coords_latlon[0]
    half, acc = total / 2.0, 0.0
    for i, s in enumerate(seg):
        if acc + s >= half:
            t = (half - acc) / s if s > 0 else 0.0
            (la1, lo1), (la2, lo2) = coords_latlon[i], coords_latlon[i + 1]
            return (la1 + t * (la2 - la1), lo1 + t * (lo2 - lo1))
        acc += s
    return coords_latlon[-1]


def main() -> int:
    db = sqlite3.connect(DB)
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")

    for rid, (plat, plon, tlat, tlon, desc, osm_river, gb) in REACHES.items():
        putin, takeout = (plat, plon), (tlat, tlon)
        print(
            f"\n=== reach {rid}: {putin} -> {takeout}"
            f" ({'OSM:' + osm_river if osm_river else 'NHD'}) ==="
        )
        if osm_river:
            coords, source = osm_impl.trace_reach(putin, takeout, river=osm_river, verbose=True)
            print(f"geometry source: {source}")
        else:
            coords = impl.trace_reach(putin, takeout, verbose=True)
        if not coords:
            print(f"ERROR: no trace for reach {rid}", file=sys.stderr)
            return 1
        miles = impl.total_distance(coords)
        geom = ",".join(f"{lon:.6f} {lat:.6f}" for (lat, lon) in coords)
        mid = arc_length_midpoint(coords)
        print(f"vertices={len(coords)} length={miles:.2f} mi midpoint={mid}")

        db.execute(
            "UPDATE reach SET geom=?, length=?, latitude=?, longitude=?,"
            " latitude_start=?, longitude_start=?, latitude_end=?, longitude_end=?,"
            " updated_at=? WHERE id=?",
            (
                geom,
                round(miles, 1),
                round(mid[0], 6),
                round(mid[1], 6),
                plat,
                plon,
                tlat,
                tlon,
                now,
                rid,
            ),
        )
        if desc:
            db.execute("UPDATE reach SET description=? WHERE id=?", (desc, rid))
        if gb:
            page, run = gb
            # reach_guidebook's composite PK is (reach_id, guidebook_id) —
            # page/run are payload — so the idempotent form is an upsert:
            # first run inserts, a re-run with corrected page/run updates.
            # (An earlier revision used a NOT EXISTS guard on the full tuple,
            # under the mistaken belief the table had no unique constraint —
            # that shape crashes on a page/run correction.)
            db.execute(
                "INSERT INTO reach_guidebook (reach_id, guidebook_id, page, run)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(reach_id, guidebook_id)"
                " DO UPDATE SET page = excluded.page, run = excluded.run",
                (rid, BENNETT_WA, page, run),
            )

    db.execute("UPDATE reach SET display_name='Green' WHERE id=429")
    # Sort corrections (takes 1-2): NF Tilton ahead of the Tilton runs; the
    # Toutle group as NF -> SF -> mainstem; Collawash out of the Clackamas
    # group into its own, between Clark Fork and Coquille.
    for rid, sort_name in [
        (432, "Tilton NF 0"),
        (427, "Toutle ag 01"),
        (428, "Toutle ag 02"),
        (426, "Toutle ag 03"),
        (321, "Collawash 01"),
        (319, "Collawash 02"),
    ]:
        db.execute("UPDATE reach SET sort_name=? WHERE id=?", (sort_name, rid))
    db.commit()
    print("\nall reach updates applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
