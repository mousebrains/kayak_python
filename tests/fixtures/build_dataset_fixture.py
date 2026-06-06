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

Run:  python3 tests/fixtures/build_dataset_fixture.py --source <kayak_data dir>
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from kayak.dataset import layout

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
# Contract tables the fixture has no rows for — written header-only so the
# fixture is a *complete projection* (every contract CSV present; absence would
# be corruption, not "not applicable"). See kayak.dataset.layout.
EMPTY_TABLES = ("rating", "rating_data", "guidebook", "reach_guidebook", "huc_name")

# id_counters covers every id-bearing contract table — including the empty ones
# (their counter records the never-reused high-water mark; next_id=1 = no rows).
ID_COUNTERS = {
    "state": 3,
    "fetch_url": 2,
    "calc_expression": 2,
    "source": 4,
    "gauge": 4,
    "reach": 4,
    "reach_class": 4,
    "rating": 1,
    "guidebook": 1,
}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _grad_str(value: object) -> str:
    """Canonical string form of a gradient-profile entry, for hashing.

    The reaches-gradient.json values are JSON-encoded strings; fall back to a
    sorted-key dump for any non-string so the digest is deterministic.
    """
    return (
        value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
    )


def _source_commit(src: Path) -> str:
    """The source dataset's git commit, for the provenance manifest."""
    try:
        return subprocess.run(
            ["git", "-C", str(src), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _require_clean_source(src: Path, rel_paths: list[str]) -> None:
    """Refuse to build from a source with uncommitted edits to the files we copy.

    The provenance manifest pins the source HEAD, so reading a dirty working
    tree would record a commit that does not match the bytes we copied. Abort
    rather than mint a fixture whose recorded commit is a lie.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(src), "status", "--porcelain", "--", *rel_paths],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise SystemExit(f"cannot verify source checkout is clean ({exc})") from exc
    if out:
        raise SystemExit(
            f"source {src} has uncommitted changes to {rel_paths}:\n{out}\n"
            "commit or stash them — the fixture pins the source HEAD."
        )


def _write_csv(name: str, header: list[str], rows: list[dict]) -> None:
    # LF line endings (DictWriter defaults to CRLF) — git diff --check clean.
    with (OUT / name).open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=header, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in header})


def _table_csv(table: str, rows: list[dict]) -> None:
    """Write a CSV whose header is the shared layout descriptor's column order."""
    _write_csv(f"{table}.csv", layout.ordered_columns(table), rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--source",
        default=os.environ.get("KAYAK_DATA_DIR"),
        help="Source dataset directory to copy public-domain geometry/facts from "
        "(or set KAYAK_DATA_DIR). No hardcoded default — must be provided.",
    )
    args = ap.parse_args(argv)
    if not args.source:
        ap.error("a --source dataset directory (or KAYAK_DATA_DIR) is required")
    src = Path(args.source)
    # The files we copy public-domain bytes from must be committed, so the
    # recorded source HEAD matches what we read.
    _require_clean_source(src, ["reach.csv", "reaches.json", "reaches-gradient.json"])

    OUT.mkdir(parents=True, exist_ok=True)
    src_reach = {r["id"]: r for r in csv.DictReader((src / "reach.csv").open(encoding="utf-8"))}
    src_geom = json.loads((src / "reaches.json").read_text())
    src_grad = json.loads((src / "reaches-gradient.json").read_text())

    reach_rows: list[dict] = []
    geom_out: dict[str, str] = {}
    grad_out: dict[str, object] = {}
    provenance_reaches: list[dict] = []
    for src_id, fid, gauge_id, name, disp, sort, river, basin, diff in REACH_PICKS:
        src_row = src_reach[src_id]
        # Provenance guard: only copy from aw_id-NULL reaches (no AW-derived
        # reach record). A future source edit that adds an aw_id fails the build.
        if (src_row.get("aw_id") or "").strip():
            raise SystemExit(
                f"source reach {src_id} has aw_id={src_row['aw_id']!r}; "
                "fixture geometry must come from aw_id-NULL reaches"
            )
        row = dict.fromkeys(layout.ordered_columns("reach"), "")
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
            row[col] = src_row[col]
        reach_rows.append(row)
        geom_out[str(fid)] = src_geom[src_id]
        if src_id in src_grad:
            grad_out[str(fid)] = src_grad[src_id]
        provenance_reaches.append(
            {
                "source_reach_id": src_id,
                "fixture_reach_id": fid,
                "geom_sha256": _sha256(src_geom[src_id]),
                "facts_sha256": _sha256("|".join(f"{c}={src_row[c]}" for c in COPIED_REACH_COLS)),
                # "" means the source reach carries no gradient profile.
                "gradient_sha256": _sha256(_grad_str(src_grad[src_id]))
                if src_id in src_grad
                else "",
            }
        )

    for g in GAUGES:
        g.setdefault("allow_negative_flow", 0)
    _table_csv("state", STATES)
    _table_csv("fetch_url", FETCH_URLS)
    _table_csv("calc_expression", CALC_EXPRESSIONS)
    _table_csv("source", SOURCES)
    _table_csv("gauge", GAUGES)
    _table_csv("gauge_source", [{"gauge_id": g, "source_id": s} for g, s in GAUGE_SOURCE])
    _table_csv("reach", reach_rows)
    _table_csv("reach_state", [{"reach_id": r, "state_id": s} for r, s in REACH_STATE])
    _table_csv("reach_class", REACH_CLASS)
    _table_csv("class_description", CLASS_DESCRIPTION)
    for empty in EMPTY_TABLES:  # header-only — complete projection
        _table_csv(empty, [])
    _write_csv(
        "id_counters.csv",
        ["table", "next_id"],
        [{"table": t, "next_id": n} for t, n in ID_COUNTERS.items()],
    )
    (OUT / "reaches.json").write_text(json.dumps(geom_out, indent=0) + "\n")
    (OUT / "reaches-gradient.json").write_text(json.dumps(grad_out, indent=0) + "\n")

    # Provenance manifest: the source revision + per-reach digests, so a later
    # regeneration that drifts from the recorded source is detectable.
    provenance = {
        "note": "Geometry/gradients copied from aw_id-NULL reaches (NHD/3DEP public "
        "domain); all editorial text is fixture-authored. See module docstring.",
        "source_repo": "kayak_data",
        "source_commit": _source_commit(src),
        "reaches": provenance_reaches,
    }
    (OUT / "PROVENANCE.json").write_text(json.dumps(provenance, indent=2) + "\n")

    print(
        f"wrote fixture to {OUT} from {src} @ {provenance['source_commit'][:12]}: "
        f"{len(reach_rows)} reaches, {len(GAUGES)} gauges, {len(SOURCES)} sources"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
