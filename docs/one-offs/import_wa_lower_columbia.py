#!/usr/bin/env python3
"""Wire the SW-Washington (lower Columbia) gauges, calcs, and AW reaches.

Implements docs/PLAN_wa_kalama_coweeman_toutle_tilton.md against the LOCAL dev
DB; the canonical kayak_data CSVs are then produced by
scripts/export_metadata.py (id_counters bumped by hand). Inserts:

- 3 live USGS gauges + sources (agency USGS, OGC-fetched): Tilton 14236200,
  Toutle at Tower Rd 14242580, NF Toutle below SRS 14240525, with NWPS lids
  TILW1 / TOTW1 / SRBW1 in nwsli_id.
- 4 calc gauges + sources + calc_expressions at the retired USGS target
  sites: SF Toutle (14241500), Kalama bl Italian Cr (14223500), Coweeman nr
  Kelso (14245000), Green ab Beaver Cr (14240800). Coefficients from the
  docs/regression/ fits named in each provenance_slug.
- 11 AW reaches (geometry from the AW GraphQL API) + reach_state WA.

Reproduce:
    python3 docs/one-offs/import_wa_lower_columbia.py --db /Users/pat/tpw/DB/kayak.db
    python3 scripts/recompute_midpoints.py --db /Users/pat/tpw/DB/kayak.db --apply
    # HUC12s: see the wbd.gpkg point-lookup block in the plan doc
    python3 scripts/export_metadata.py --db /Users/pat/tpw/DB/kayak.db --out <kayak_data>
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.request
from datetime import UTC, datetime

GRAPHQL_URL = "https://www.americanwhitewater.org/graphql"

# --- id allocation (kayak_data id_counters as of source 350 / gauge 227 /
# calc_expression 20 / reach 422; bump counters to 357/234/24/433) -----------

GAUGES_USGS = [
    # id, site, location, lat, lon, nwsli, elev, da, huc12, river, display, sort
    (
        227,
        "14236200",
        "Above Bear Canyon Cr nr Cinebar",
        46.595384,
        -122.459556,
        "TILW1",
        610.48,
        141.0,
        "170800050106",
        "Tilton",
        "Tilton nr Cinebar",
        "tilton|9|009389|000141",
    ),
    (
        228,
        "14242580",
        "At Tower Rd nr Silver Lake",
        46.335,
        -122.840833,
        "TOTW1",
        120.0,
        496.0,
        "170800050702",
        "Toutle",
        "Toutle at Tower Rd",
        "toutle|9|009880|000496",
    ),
    (
        229,
        "14240525",
        "Below SRS nr Kid Valley",
        46.371775,
        -122.578999,
        "SRBW1",
        700.0,
        146.0,
        "170800050504",
        "North Fork Toutle",
        "NF Toutle below SRS",
        "toutle|0north|009300|000146",
    ),
]
SOURCES_USGS = [(350, "14236200"), (351, "14242580"), (352, "14240525")]

CALCS = [
    # calc_id, expression, time_expression, note, provenance_slug
    (
        20,
        "round(greatest(0, 0.259162 * tw::14242580::flow + 0.210774 * "
        "ef::EF_Lewis_Washington_merge::flow -75.92))",
        "tw::14242580::flow ef::EF_Lewis_Washington_merge::flow",
        "SF Toutle at Toutle estimated from Toutle at Tower Rd (14242580, downstream "
        "mass-balance - it contains the SF flow) and EF Lewis nr Heisson (14222500) via "
        "OLS fit against USGS 14241500 (retired 2013-09-29). Post-SRS window "
        "1989-10-01..2013-09-29, n=6451 daily means. r2=0.9441, RMSE=188.8 cfs "
        "(Tower-only baseline 0.9358 / 202.4). Tower CI [+0.236, +0.283], EF Lewis CI "
        "[+0.162, +0.263] (monthly-block bootstrap). Rejected: NF-SRS 14240525 (marginal "
        "given Tower and cuts the sample by a third), Tilton (weaker than EF Lewis), all "
        "quadratic terms (CIs straddle 0). Pre-1989 record excluded - 1980 eruption and "
        "SRS construction make the early Toutle series non-stationary. Co-located with "
        "the historical USGS site, no drainage-area scaling. See "
        "docs/regression/sftoutle_14241500_from_tower_eflewis.md.",
        "sftoutle_14241500_from_tower_eflewis",
    ),
    (
        21,
        "round(greatest(0, 1.14988 * least(ef::EF_Lewis_Washington_merge::flow, 15600) "
        "+ 0.24075 * ti::14236200::flow + -3.84488e-05 * "
        "least(ef::EF_Lewis_Washington_merge::flow, 15600) * "
        "least(ef::EF_Lewis_Washington_merge::flow, 15600) +187.2))",
        "ef::EF_Lewis_Washington_merge::flow ti::14236200::flow",
        "Kalama below Italian Cr estimated from EF Lewis nr Heisson (14222500, quadratic) "
        "and Tilton ab Bear Canyon Cr (14236200) via OLS fit against USGS 14223500 "
        "(retired 1982-09-12). Window 1956-10-01..1982-09-12, n=7199 daily means. "
        "r2=0.9349, RMSE=312.6 cfs (EF-only linear baseline 0.9148 / 357.5). EF CI "
        "[+1.074, +1.239], Tilton CI [+0.187, +0.303], EF^2 CI [-5.71e-05, -3.03e-05] "
        "(monthly-block bootstrap; the concavity halves the dry-season bias and is "
        "decisively significant). DEPLOYMENT CAP: every EF reference is "
        "least(ef, 15600) - the fitted parabola's vertex sits at EF=14955 cfs just "
        "inside the observed range [30, 15600], so an uncapped major EF flood "
        "(>20000 cfs has occurred) would drive the estimate toward zero; with the cap "
        "it plateaus at the calibrated edge. Fit calibrated on 1956-82 data - "
        "free-flowing basins, stationarity assumed (see the Coweeman note for the "
        "out-of-era evidence on the same donor). See "
        "docs/regression/kalama_14223500_from_eflewis_tilton.md.",
        "kalama_14223500_from_eflewis_tilton",
    ),
    (
        22,
        "round(greatest(0, 0.574501 * ef::EF_Lewis_Washington_merge::flow + "
        "-9.5595e-06 * ef::EF_Lewis_Washington_merge::flow * "
        "ef::EF_Lewis_Washington_merge::flow -4.64))",
        "ef::EF_Lewis_Washington_merge::flow",
        "Coweeman nr Kelso estimated from EF Lewis nr Heisson (14222500, quadratic) via "
        "OLS fit against USGS 14245000 (retired 1984-09-30). Window "
        "1950-10-01..1984-09-30, n=12417 daily means. r2=0.8975, RMSE=173.7 cfs (linear "
        "baseline 0.8946 / 176.1; the quadratic improves the dry-season bias -18.0% to "
        "-11.1%). EF^2 CI [-1.48e-05, -2.38e-06] excludes 0; parabola vertex at "
        "EF=30000 cfs is beyond any observed flow, so no input cap is needed. Tilton "
        "rejected as a second donor (significant but ~1% RMSE gain). OUT-OF-ERA "
        "VALIDATION: scored against the independent WA Ecology 26C075 telemetry record "
        "(2006-2019, n=3643, never seen by the fit): bias -1.1% of mean, r2=0.8910, "
        "RMSE=156.4 cfs - the 1950s-era relationship holds 25+ years later "
        "(docs/one-offs/coweeman_doe_validation.py). See "
        "docs/regression/coweeman_14245000_from_eflewis.md.",
        "coweeman_14245000_from_eflewis",
    ),
    (
        23,
        "round(greatest(0, 0.204922 * tw::14242580::flow + 0.108959 * ti::14236200::flow +0.6843))",
        "tw::14242580::flow ti::14236200::flow",
        "Green River (Toutle drainage) above Beaver Cr estimated from Toutle at Tower Rd "
        "(14242580, downstream mass-balance) and Tilton ab Bear Canyon Cr (14236200) via "
        "OLS fit against USGS 14240800 (retired 1994-09-29). Post-SRS window "
        "1989-10-01..1994-09-29, n=1825 daily means (the 1981-88 eruption-recovery era "
        "is excluded; full-window refit drifts coefficients only ~8-13%). r2=0.9530, "
        "RMSE=101.4 cfs (Tower-only baseline 0.9397 / 114.8). Tower CI [+0.175, +0.234], "
        "Tilton CI [+0.055, +0.170] (monthly-block bootstrap). NF-SRS rejected (CI "
        "[-0.211, +0.105] straddles 0). Co-located with the historical USGS site at the "
        "AW run's take-out, no drainage-area scaling. See "
        "docs/regression/green_14240800_from_tower_tilton.md.",
        "green_14240800_from_tower_tilton",
    ),
]

GAUGES_CALC = [
    # id, name, location, lat, lon, elev, da, huc12, river, display, sort, src_id, calc_id
    (
        230,
        "SF_Toutle_calc",
        "At Toutle (estimated)",
        46.322055,
        -122.697055,
        400.0,
        120.0,
        "170800050605",
        "South Fork Toutle",
        "SF Toutle at Toutle (calc)",
        "toutle|0south|009600|000120",
        353,
        20,
    ),
    (
        231,
        "Kalama_ItalianCreek_calc",
        "Below Italian Cr nr Kalama (estimated)",
        46.044836,
        -122.815384,
        20.0,
        198.0,
        "170800030305",
        "Kalama",
        "Kalama bl Italian Cr (calc)",
        "kalama|9|009980|000198",
        354,
        21,
    ),
    (
        232,
        "Coweeman_Kelso_calc",
        "Near Kelso (estimated)",
        46.128169,
        -122.838443,
        30.0,
        119.0,
        "170800050804",
        "Coweeman",
        "Coweeman nr Kelso (calc)",
        "coweeman|9|009970|000119",
        355,
        22,
    ),
    (
        233,
        "Green_Toutle_calc",
        "Above Beaver Cr nr Kid Valley (estimated)",
        46.381775,
        -122.523720,
        None,
        129.0,
        "170800050404",
        "Green (Toutle)",
        "Green (Toutle) ab Beaver Cr (calc)",
        "green (toutle)|9|999999|000129",
        356,
        23,
    ),
]

REACHES = [
    # reach_id, aw_id, gauge_id, display, sort, river, basin
    (422, 2139, 231, "Kalama", "Kalama ag 01", "Kalama", "Kalama"),
    (423, 2141, 231, "Kalama", "Kalama ag 02", "Kalama", "Kalama"),
    (424, 2140, 231, "Kalama", "Kalama ag 03", "Kalama", "Kalama"),
    (425, 3480, 232, "Coweeman", "Coweeman ag 01", "Coweeman", "Cowlitz"),
    (426, 2253, 228, "Toutle", "Toutle ag 01", "Toutle", "Cowlitz"),
    (427, 3509, 229, "NF Toutle", "Toutle ag 02", "North Fork Toutle", "Cowlitz"),
    (428, 2254, 230, "SF Toutle", "Toutle ag 03", "South Fork Toutle", "Cowlitz"),
    (429, 2122, 233, "Green (Toutle)", "Green (Toutle) ag 01", "Green (Toutle)", "Cowlitz"),
    (430, 3067, 227, "Tilton", "Tilton ag 01", "Tilton", "Cowlitz"),
    (431, 3411, 227, "Tilton", "Tilton ag 02", "Tilton", "Cowlitz"),
]
REACH_NF_TILTON = (432, 3430, 227, "NF Tilton", "Tilton ag 03", "North Fork Tilton", "Cowlitz")
WA_STATE_ID = 5


AW_CACHE = "/Users/pat/tpw/kayak/Gauge-metadata-cache/gauges.db"


def fetch_aw_geom(aw_ids: list[int]) -> dict[int, str | None]:
    """Geometry per AW id via one aliased GraphQL query (the proven pattern
    from docs/one-offs/import_aw_usgs_reaches.py::fetch_aw_geom_batch)."""
    q = "{\n" + "\n".join(f"r{a}: reach(id: {a}) {{ geom }}" for a in aw_ids) + "\n}"
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=json.dumps({"query": q}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "kayak-import"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.load(r)["data"]
    return {a: (data.get(f"r{a}") or {}).get("geom") for a in aw_ids}


def fetch_aw(aw_ids: list[int]) -> dict[int, dict]:
    """Reach attributes from the local AW cache + geometry from GraphQL."""
    cache = sqlite3.connect(AW_CACHE)
    out: dict[int, dict] = {}
    for a in aw_ids:
        row = cache.execute(
            "SELECT section, class, length, avg_gradient, max_gradient,"
            " put_in_lat, put_in_lon, take_out_lat, take_out_lon,"
            " begin_low_runnable, end_high_runnable"
            " FROM aw_reach WHERE id = ?",
            (a,),
        ).fetchone()
        if row is None:
            raise SystemExit(f"AW reach {a} not in cache {AW_CACHE}")
        out[a] = {
            "section": row[0],
            "class": row[1],
            "length": row[2],
            "avg_gradient": row[3],
            "max_gradient": row[4],
            "p_lat": row[5],
            "p_lon": row[6],
            "t_lat": row[7],
            "t_lon": row[8],
            "low_runnable": row[9],
            "high_runnable": row[10],
        }
    cache.close()
    for a, geom in fetch_aw_geom(aw_ids).items():
        out[a]["geom"] = geom
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    args = ap.parse_args()
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")

    reaches = [*REACHES, REACH_NF_TILTON]
    aw = fetch_aw([r[1] for r in reaches])

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    for sid, site in SOURCES_USGS:
        cur.execute("INSERT INTO source (id, name, agency) VALUES (?, ?, 'USGS')", (sid, site))
    for gid, site, loc, lat, lon, nwsli, elev, da, huc, river, disp, sort in GAUGES_USGS:
        cur.execute(
            "INSERT INTO gauge (id, name, location, latitude, longitude, nwsli_id,"
            " usgs_id, elevation, drainage_area, huc, allow_negative_flow, river,"
            " display_name, sort_name, state)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?,?,'WA')",
            (gid, site, loc, lat, lon, nwsli, site, elev, da, huc, river, disp, sort),
        )
    for (gid, *_), (sid, _) in zip(GAUGES_USGS, SOURCES_USGS, strict=True):
        cur.execute("INSERT INTO gauge_source (gauge_id, source_id) VALUES (?,?)", (gid, sid))

    for cid, expr, texpr, note, slug in CALCS:
        cur.execute(
            "INSERT INTO calc_expression (id, data_type, expression, time_expression,"
            " note, provenance_slug) VALUES (?, 'flow', ?, ?, ?, ?)",
            (cid, expr, texpr, note, slug),
        )
    for gid, name, loc, lat, lon, elev, da, huc, river, disp, sort, sid, cid in GAUGES_CALC:
        cur.execute(
            "INSERT INTO source (id, name, agency, calc_expression_id)"
            " VALUES (?, ?, 'Calculation', ?)",
            (sid, name, cid),
        )
        cur.execute(
            "INSERT INTO gauge (id, name, location, latitude, longitude, usgs_id,"
            " elevation, drainage_area, huc, allow_negative_flow, river, display_name,"
            " sort_name, state) VALUES (?,?,?,?,?,NULL,?,?,?,0,?,?,?,'WA')",
            (gid, name, loc, lat, lon, elev, da, huc, river, disp, sort),
        )
        cur.execute("INSERT INTO gauge_source (gauge_id, source_id) VALUES (?,?)", (gid, sid))

    for rid, aw_id, gid, disp, sort, river, basin in reaches:
        info = aw[aw_id]
        length = info["length"]
        grad = info["avg_gradient"]
        elev_lost = round(length * grad) if (length and grad) else None
        cur.execute(
            "INSERT INTO reach (id, updated_at, gauge_id, name, display_name,"
            " sort_name, description, difficulties, basin, elevation_lost, length,"
            " gradient, latitude_start, longitude_start, latitude_end, longitude_end,"
            " no_show, aw_id, river, max_gradient, geom, map_only)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,0)",
            (
                rid,
                now,
                gid,
                f"aw_{aw_id}",
                disp,
                sort,
                info["section"],
                info["class"],
                basin,
                elev_lost,
                length,
                grad,
                info["p_lat"],
                info["p_lon"],
                info["t_lat"],
                info["t_lon"],
                aw_id,
                river,
                info["max_gradient"],
                info["geom"],
            ),
        )
        cur.execute(
            "INSERT INTO reach_state (reach_id, state_id) VALUES (?, ?)",
            (rid, WA_STATE_ID),
        )
        # One class band per reach; runnable bounds from AW where published
        # (reach_class ids continue from the table max, 431 before this run).
        cur.execute(
            "INSERT INTO reach_class (id, reach_id, name, low, low_data_type,"
            " high, high_data_type)"
            " VALUES ((SELECT MAX(id) + 1 FROM reach_class), ?, ?, ?, 'flow', ?, 'flow')",
            (rid, info["class"], info["low_runnable"], info["high_runnable"]),
        )

    conn.commit()
    print(
        f"inserted: {len(SOURCES_USGS) + len(GAUGES_CALC)} sources, "
        f"{len(GAUGES_USGS) + len(GAUGES_CALC)} gauges, {len(CALCS)} calcs, "
        f"{len(reaches)} reaches (geom from AW for "
        f"{sum(1 for r in reaches if aw[r[1]].get('geom'))})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
