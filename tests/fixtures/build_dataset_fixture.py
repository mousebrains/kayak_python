#!/usr/bin/env python3
"""Generate the redistribution-safe fixture dataset under tests/fixtures/dataset/.

This is the provenance record for the fixture (S4a-1 of the dataset-separation
plan). The committed fixture is the artifact tests read; this script documents
exactly how it was produced and lets it be regenerated.

Provenance / licensing:
  - Reach GEOMETRY (reach.geom, reaches.json) and the gradient profiles are
    copied verbatim from real reaches in the kayak_data snapshot. That geometry
    is NHD HR-derived (USGS, public domain) and the gradients are USGS 3DEP-
    derived (public domain) — redistribution-safe.
  - Numeric facts (length, elevation, endpoints, HUC) are likewise factual.
  - All EDITORIAL text (reach name/display_name/description/river/basin,
    gauge names, calc expression, class descriptions, site prose) is authored
    for this fixture — no WKCC/American Whitewater prose is copied. The source
    reaches are all aw_id-NULL (no AW-derived reach record) as a second guard.

The fixture is a small, self-consistent dataset that exercises every loader and
validator path: two states, three gauges (USGS-OGC / URL-backed / calculated),
one fetch URL, one calc expression, three reaches with real geometry, plus the
junction and class tables.

Run:  python3 tests/fixtures/build_dataset_fixture.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

# Source snapshot (sibling clone) — only read for public-domain geometry/facts.
SRC = Path("/Users/pat/tpw/kayak_data")
OUT = Path(__file__).resolve().parent / "dataset"

# Deterministic timestamp — a fixture must not churn.
TS = "2026-01-01 00:00:00"

# Selected real reaches: (source_reach_id, fixture overrides). Geometry + the
# factual numeric columns are copied from the source row; everything editorial
# is replaced. All three sources are aw_id-NULL Oregon reaches.
REACH_PICKS = [
    # src_id, fixture_id, gauge_id, name, display_name, sort_name, river, basin, difficulties
    (
        "43",
        1,
        1,
        "fixture_cascade_bridge",
        "Cascade River — Bridge to Forks",
        "Cascade 01",
        "Cascade River",
        "Cascade",
        "II",
    ),
    (
        "53",
        2,
        3,
        "fixture_ridge_canyon",
        "Ridge Creek — Canyon",
        "Ridge Creek 01",
        "Ridge Creek",
        "Cascade",
        "III-IV",
    ),
    (
        "38",
        3,
        1,
        "fixture_valley_lower",
        "Valley River — Lower",
        "Valley River 01",
        "Valley River",
        "Valley",
        "I-II",
    ),
]
# reach.csv columns copied verbatim from the source row (factual, public domain).
COPIED_REACH_COLS = [
    "basin_area",
    "elevation",
    "elevation_lost",
    "length",
    "gradient",
    "latitude",
    "longitude",
    "latitude_start",
    "longitude_start",
    "latitude_end",
    "longitude_end",
    "max_gradient",
    "huc",
]

REACH_HEADER = [
    "id",
    "updated_at",
    "gauge_id",
    "name",
    "display_name",
    "sort_name",
    "nature",
    "description",
    "difficulties",
    "basin",
    "basin_area",
    "elevation",
    "elevation_lost",
    "length",
    "gradient",
    "features",
    "latitude",
    "longitude",
    "latitude_start",
    "longitude_start",
    "latitude_end",
    "longitude_end",
    "no_show",
    "notes",
    "optimal_flow",
    "region",
    "remoteness",
    "scenery",
    "season",
    "watershed_type",
    "aw_id",
    "river",
    "max_gradient",
    "huc",
    "map_only",
    "no_flow_range",
    "gradient_unreliable",
]

STATES = [
    {"id": 1, "name": "Oregon", "abbreviation": "OR"},
    {"id": 2, "name": "Washington", "abbreviation": "WA"},
]
FETCH_URLS = [
    {
        "id": 1,
        "url": "https://api.water.noaa.gov/nwps/v1/gauges/FXTW1/stageflow/observed",
        "parser": "nwps",
        "hours": "",
        "is_active": 1,
    },
]
CALC_EXPRESSIONS = [
    {
        "id": 1,
        "data_type": "flow",
        "expression": "round(greatest(0, 0.5 * fx::14150000::flow))",
        "time_expression": "fx::14150000::flow",
        "note": "fixture calc: half of the USGS gauge",
        "provenance_slug": "",
    },
]
SOURCES = [
    {
        "id": 1,
        "name": "14150000",
        "agency": "USGS",
        "fetch_url_id": "",
        "calc_expression_id": "",
        "timezone": "",
    },
    {
        "id": 2,
        "name": "FXTW1",
        "agency": "NWS",
        "fetch_url_id": 1,
        "calc_expression_id": "",
        "timezone": "",
    },
    {
        "id": 3,
        "name": "Fixture_Confluence_calc",
        "agency": "Calculation",
        "fetch_url_id": "",
        "calc_expression_id": 1,
        "timezone": "",
    },
]
# gauge rows (23 cols); blanks for unused.
GAUGES = [
    {
        "id": 1,
        "name": "14150000",
        "usgs_id": "14150000",
        "location": "near Bridge",
        "latitude": 44.0726,
        "longitude": -122.9652,
        "elevation": 460.0,
        "drainage_area": 900.0,
        "huc": "170900040706",
        "river": "Cascade River",
        "display_name": "Cascade River near Bridge",
        "sort_name": "cascade|9|009540|000900",
        "state": "OR",
    },
    {
        "id": 2,
        "name": "FXTW1",
        "nwsli_id": "FXTW1",
        "location": "at Fixture Falls",
        "latitude": 46.5000,
        "longitude": -122.4000,
        "elevation": 600.0,
        "drainage_area": 140.0,
        "huc": "170800050106",
        "river": "Fixture Creek",
        "display_name": "Fixture Creek at Falls",
        "sort_name": "fixture creek|9|009400|000140",
        "state": "WA",
    },
    {
        "id": 3,
        "name": "Fixture_Confluence_calc",
        "location": "At confluence (estimated)",
        "latitude": 45.1387,
        "longitude": -122.5335,
        "elevation": 366.0,
        "drainage_area": 200.0,
        "huc": "170900090607",
        "river": "Ridge Creek",
        "display_name": "Ridge Creek at confluence (calc)",
        "sort_name": "ridge creek|9|009634|000200",
        "state": "OR",
    },
]
GAUGE_HEADER = [
    "id",
    "name",
    "bank_full",
    "flood_stage",
    "location",
    "latitude",
    "longitude",
    "station_id",
    "cbtt_id",
    "geos_id",
    "nws_id",
    "nwsli_id",
    "snotel_id",
    "usgs_id",
    "rating_id",
    "elevation",
    "drainage_area",
    "huc",
    "allow_negative_flow",
    "river",
    "display_name",
    "sort_name",
    "state",
]
GAUGE_SOURCE = [(1, 1), (2, 2), (3, 3)]
REACH_STATE = [(1, 1), (2, 1), (3, 1)]  # all three reaches in Oregon
REACH_CLASS = [
    {
        "id": 1,
        "reach_id": 1,
        "name": "II",
        "low": 500.0,
        "low_data_type": "flow",
        "high": 3000.0,
        "high_data_type": "flow",
    },
    {
        "id": 2,
        "reach_id": 2,
        "name": "III-IV",
        "low": 800.0,
        "low_data_type": "flow",
        "high": 5000.0,
        "high_data_type": "flow",
    },
    {
        "id": 3,
        "reach_id": 3,
        "name": "I-II",
        "low": "",
        "low_data_type": "flow",
        "high": "",
        "high_data_type": "flow",
    },
]
CLASS_DESCRIPTION = [
    {"name": "I-II", "description": "Fixture class text: easy moving water with riffles."},
    {"name": "II", "description": "Fixture class text: straightforward rapids, wide channels."},
    {"name": "III-IV", "description": "Fixture class text: powerful rapids requiring maneuvering."},
]
# id_counters covers every fixture table with an id column.
ID_COUNTERS = {
    "state": 3,
    "fetch_url": 2,
    "calc_expression": 2,
    "source": 4,
    "gauge": 4,
    "reach": 4,
    "reach_class": 4,
}


def _write_csv(name: str, header: list[str], rows: list[dict]) -> None:
    with (OUT / name).open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in header})


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    src_reach = {r["id"]: r for r in csv.DictReader((SRC / "reach.csv").open(encoding="utf-8"))}
    src_geom = json.loads((SRC / "reaches.json").read_text())
    src_grad = json.loads((SRC / "reaches-gradient.json").read_text())

    reach_rows: list[dict] = []
    geom_out: dict[str, str] = {}
    grad_out: dict[str, object] = {}
    for src_id, fid, gauge_id, name, disp, sort, river, basin, diff in REACH_PICKS:
        src = src_reach[src_id]
        row = {c: "" for c in REACH_HEADER}
        row.update(
            id=fid,
            updated_at=TS,
            gauge_id=gauge_id,
            name=name,
            display_name=disp,
            sort_name=sort,
            description=disp.split("—")[-1].strip(),
            difficulties=diff,
            basin=basin,
            river=river,
            aw_id="",
            no_show=0,
            map_only=0,
            no_flow_range=0,
            gradient_unreliable=0,
        )
        for col in COPIED_REACH_COLS:
            row[col] = src[col]
        reach_rows.append(row)
        geom_out[str(fid)] = src_geom[src_id]
        if src_id in src_grad:
            grad_out[str(fid)] = src_grad[src_id]

    _write_csv("state.csv", ["id", "name", "abbreviation"], STATES)
    _write_csv("fetch_url.csv", ["id", "url", "parser", "hours", "is_active"], FETCH_URLS)
    _write_csv(
        "calc_expression.csv",
        ["id", "data_type", "expression", "time_expression", "note", "provenance_slug"],
        CALC_EXPRESSIONS,
    )
    _write_csv(
        "source.csv",
        ["id", "name", "agency", "fetch_url_id", "calc_expression_id", "timezone"],
        SOURCES,
    )
    for g in GAUGES:
        g.setdefault("allow_negative_flow", 0)
    _write_csv("gauge.csv", GAUGE_HEADER, GAUGES)
    _write_csv(
        "gauge_source.csv",
        ["gauge_id", "source_id"],
        [{"gauge_id": g, "source_id": s} for g, s in GAUGE_SOURCE],
    )
    _write_csv("reach.csv", REACH_HEADER, reach_rows)
    _write_csv(
        "reach_state.csv",
        ["reach_id", "state_id"],
        [{"reach_id": r, "state_id": s} for r, s in REACH_STATE],
    )
    _write_csv(
        "reach_class.csv",
        ["id", "reach_id", "name", "low", "low_data_type", "high", "high_data_type"],
        REACH_CLASS,
    )
    _write_csv("class_description.csv", ["name", "description"], CLASS_DESCRIPTION)
    _write_csv(
        "id_counters.csv",
        ["table", "next_id"],
        [{"table": t, "next_id": n} for t, n in ID_COUNTERS.items()],
    )

    (OUT / "reaches.json").write_text(json.dumps(geom_out, indent=0) + "\n")
    (OUT / "reaches-gradient.json").write_text(json.dumps(grad_out, indent=0) + "\n")

    print(
        f"wrote fixture to {OUT}: {len(reach_rows)} reaches, {len(GAUGES)} gauges, "
        f"{len(SOURCES)} sources, geom for {len(geom_out)}, gradient for {len(grad_out)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
