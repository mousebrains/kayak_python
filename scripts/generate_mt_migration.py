#!/usr/bin/env python3
"""Generate 0036_montana_usgs_gauges.sql from montana/mt.list.

Build artifact — gitignored. The committed SQL is the source of truth;
re-run this to regenerate after editing the curated mt.list.

Inputs:
  montana/mt.list                              (curated USGS site numbers)
  Gauge-metadata-cache/gauges.db::usgs_site    (station metadata)

Outputs:
  data/db/migrations/0036_montana_usgs_gauges.sql

Field-derivation rules — see docs/PLAN_montana_gauges.md § Phase 2.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MT_LIST_PATH = REPO / "montana" / "mt.list"
CACHE_DB_PATH = REPO / "Gauge-metadata-cache" / "gauges.db"
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


def parse_mt_list(path: Path) -> list[str]:
    """Return USGS site numbers in the order they appear in mt.list.

    Format: tab-separated `<row#>\\t<usgs_site_no>\\t<label>`. Blank lines
    and `#`-prefixed comment lines are skipped. The first and third
    columns are for human review only; tooling reads column 2.
    """
    site_nos: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            # Tolerate space-separated rows too — column 2 is what matters.
            parts = line.split()
        if len(parts) < 2:
            raise SystemExit(f"mt.list: cannot parse row {raw!r}")
        site_no = parts[1].strip()
        if not site_no.isdigit():
            raise SystemExit(f"mt.list: column 2 not a USGS site number: {raw!r}")
        site_nos.append(site_no)
    return site_nos


def sql_escape(s: str) -> str:
    return s.replace("'", "''")


def emit_sql() -> str:
    out: list[str] = []
    out.append("-- Migration 0036: Montana USGS gauges (curated list).")
    out.append("--")
    out.append("-- Site numbers pulled from montana/mt.list (hand-curated by Pat from")
    out.append("-- the entries circled on https://levels-legacy.wkcc.org/?P=Montana.html).")
    out.append("-- Per-site metadata pulled from Gauge-metadata-cache/gauges.db::usgs_site.")
    out.append("-- See docs/PLAN_montana_gauges.md.")
    out.append("--")
    out.append("-- Idempotent: re-running is safe (INSERT OR IGNORE / WHERE NOT EXISTS).")
    out.append("")

    site_nos = parse_mt_list(MT_LIST_PATH)

    with sqlite3.connect(CACHE_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        for site_no in site_nos:
            row = conn.execute(
                "SELECT station_nm, latitude, longitude, huc_cd, "
                "drain_area_sq_mi, altitude_ft FROM usgs_site WHERE site_no = ?",
                (site_no,),
            ).fetchone()
            if row is None:
                raise SystemExit(
                    f"USGS {site_no} not in {CACHE_DB_PATH}. "
                    f"Refresh the cache: python3 scripts/fetch_usgs_sites.py"
                )
            station_nm = row["station_nm"]
            lat = row["latitude"]
            lon = row["longitude"]
            drain = row["drain_area_sq_mi"]
            elev = row["altitude_ft"]
            huc = row["huc_cd"]

            river, location, display = parse_station_name(station_nm)
            srt = sort_name(river, elev, drain)

            out.append("-- " + sql_escape(station_nm))
            out.append(
                "INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)"
            )
            out.append(f"SELECT '{site_no}', 'USGS', NULL, NULL, ''")
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
    insert_count = sql.count("INSERT INTO source")
    print(f"Wrote {SQL_PATH} ({len(sql.splitlines())} lines, {insert_count} source rows)")


if __name__ == "__main__":
    main()
