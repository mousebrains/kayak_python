#!/usr/bin/env python3
"""Generate 0036_montana_usgs_gauges.sql from data/discover/montana_candidates.csv.

Build artifact — gitignored. The committed SQL is the source of truth;
re-run this to regenerate after trimming the candidate CSV.

Outputs:
  data/db/migrations/0036_montana_usgs_gauges.sql

Field-derivation rules — see docs/PLAN_montana_gauges.md § Phase 2.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CSV_PATH = REPO / "data" / "discover" / "montana_candidates.csv"
SQL_PATH = REPO / "data" / "db" / "migrations" / "0036_montana_usgs_gauges.sql"

# USGS station-name abbreviations to expand into full English.
_ABBREV_EXPANSIONS = [
    (r"\bM F\b", "Middle Fork"),
    (r"\bS F\b", "South Fork"),
    (r"\bN F\b", "North Fork"),
    (r"\bE F\b", "East Fork"),
    (r"\bW F\b", "West Fork"),
    (r"\bMF\b", "Middle Fork"),
    (r"\bSF\b", "South Fork"),
    (r"\bNF\b", "North Fork"),
    (r"\bEF\b", "East Fork"),
    (r"\bWF\b", "West Fork"),
    (r"\bbl\b", "below"),
    (r"\bnr\b", "near"),
    (r"\bab\b", "above"),
    (r"\babv\b", "above"),
    (r"\bCr\b", "Creek"),
]

# Position words that separate river from location in USGS station_nm.
_SPLIT_RE = re.compile(
    r"\s+(at|near|above|below|ab|abv|bl|nr)\s+", re.IGNORECASE
)

# Strip a trailing state suffix from a location string.
_TAIL_STATE_RE = re.compile(r",?\s*MT\.?\s*$", re.IGNORECASE)

# Tokens in station_nm that mark a site as not paddler-relevant. Sites
# matching get a -- REVIEW: comment so Pat can decide whether to keep them.
_REVIEW_HINTS = (
    "wetland",
    "spillway",
    "canal",
    "diversion",
    "reservoir",
    "lake como",
    "silver bow",  # Berkeley Pit / Anaconda Superfund area
    "mill creek nr anaconda",
    "warm springs creek",
    "willow creek nr anaconda",
    "willow creek at opportunity",
    "lost creek",
    "blacktail creek",
)


def _expand_abbrevs(text: str) -> str:
    out = text
    for pattern, repl in _ABBREV_EXPANSIONS:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    return out


def parse_station_name(station_nm: str) -> tuple[str, str, str]:
    """Return (river, location, display_name) from a USGS station_nm.

    Best-effort. Falls back to ("", station_nm, station_nm) if nothing
    splits cleanly so the migration row isn't empty.
    """
    clean = _expand_abbrevs(station_nm.strip())
    clean = _TAIL_STATE_RE.sub("", clean).rstrip(", ")

    m = _SPLIT_RE.search(clean)
    if not m:
        return "", clean, clean

    river = clean[: m.start()].strip().rstrip(",")
    # Drop trailing "River" / "Creek" from river if it's part of a full
    # name like "Clark Fork River" → keep as "Clark Fork"; but "Tobacco
    # River" stays "Tobacco". Keep semantics simple: only strip a bare
    # trailing " River" when the river name has at least one more word.
    river = re.sub(r"\s+River$", "", river) if " " in river else river

    rel_word = m.group(1).lower()
    rel_map = {
        "at": "at", "near": "near",
        "above": "above", "ab": "above", "abv": "above",
        "below": "below", "bl": "below",
        "nr": "near",
    }
    rel = rel_map.get(rel_word, rel_word)
    location_raw = clean[m.end():].strip().rstrip(",")
    location = f"{rel} {location_raw}"
    display = f"{river} {rel} {location_raw}".strip()
    return river, location, display


def basin_slug(river: str) -> str:
    """Lowercase + strip spaces/punctuation. Empty river → 'zz_unparsed'."""
    if not river:
        return "zz_unparsed"
    slug = re.sub(r"[^a-z0-9]", "", river.lower())
    return slug or "zz_unparsed"


def sort_name(river: str, elev_ft: float | None, drain_sq_mi: float | None) -> str:
    elev_key = f"{round(10000 - elev_ft):06d}" if elev_ft is not None else "999999"
    da_key = f"{round(drain_sq_mi):06d}" if drain_sq_mi is not None else "999999"
    return f"{basin_slug(river)}|9|{elev_key}|{da_key}"


def needs_review(station_nm: str, river: str) -> bool:
    blob = f"{station_nm} {river}".lower()
    if any(h in blob for h in _REVIEW_HINTS):
        return True
    # Site numbers >9 digits (USGS extended IDs) are usually well/spring
    # monitoring rather than streams.
    return False


def sql_escape(s: str) -> str:
    return s.replace("'", "''")


def emit_sql() -> str:
    out: list[str] = []
    out.append(
        "-- Migration 0036: Montana USGS gauges in HUC4 1701 (Pacific drainage)."
    )
    out.append("--")
    out.append(
        "-- Pulled from data/discover/montana_candidates.csv (7-day-active sites,"
    )
    out.append(
        "-- HUC4 1701 ∩ state=MT). See docs/PLAN_montana_gauges.md."
    )
    out.append("--")
    out.append("-- Idempotent: re-running is safe (INSERT OR IGNORE / WHERE NOT EXISTS).")
    out.append("")

    with CSV_PATH.open() as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            site_no = row["site_no"]
            station_nm = row["station_nm"]
            try:
                lat = float(row["latitude"])
                lon = float(row["longitude"])
            except ValueError:
                continue
            try:
                drain = float(row["drain_area_sq_mi"]) if row["drain_area_sq_mi"] else None
            except ValueError:
                drain = None
            try:
                elev = float(row["altitude_ft"]) if row["altitude_ft"] else None
            except ValueError:
                elev = None
            huc = row["huc_cd"]

            river, location, display = parse_station_name(station_nm)
            srt = sort_name(river, elev, drain)
            review = " -- REVIEW: industrial / monitoring site?" if needs_review(station_nm, river) else ""

            out.append(
                "-- " + sql_escape(station_nm) + review
            )
            out.append(
                f"INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)"
            )
            out.append(
                f"SELECT '{site_no}', 'USGS', NULL, NULL, ''"
            )
            out.append(
                f"WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '{site_no}' AND agency = 'USGS');"
            )
            drain_sql = "NULL" if drain is None else str(drain)
            elev_sql = "NULL" if elev is None else str(elev)
            out.append(
                "INSERT OR IGNORE INTO gauge ("
                "name, location, latitude, longitude, usgs_id, station_id, "
                "river, display_name, sort_name, drainage_area, elevation, "
                "huc, allow_negative_flow, state) VALUES ("
                f"'{site_no}', '{sql_escape(location)}', {lat}, {lon}, "
                f"'{site_no}', '{site_no}', "
                f"'{sql_escape(river)}', '{sql_escape(display)}', '{srt}', "
                f"{drain_sql}, {elev_sql}, "
                f"'{huc}', 0, 'MT');"
            )
            out.append(
                "INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) "
                "SELECT g.id, s.id FROM gauge g, source s "
                f"WHERE g.name = '{site_no}' AND s.name = '{site_no}' AND s.agency = 'USGS';"
            )
            out.append("")
    return "\n".join(out) + "\n"


def main() -> None:
    SQL_PATH.parent.mkdir(parents=True, exist_ok=True)
    sql = emit_sql()
    SQL_PATH.write_text(sql)
    print(f"Wrote {SQL_PATH} ({len(sql.splitlines())} lines)")
    review_count = sum(1 for line in sql.splitlines() if "REVIEW:" in line)
    insert_count = sql.count("INSERT INTO source")
    print(f"  {insert_count} source rows, {review_count} flagged for REVIEW")


if __name__ == "__main__":
    main()
