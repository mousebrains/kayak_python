#!/usr/bin/env python3
"""Populate gauge.river / gauge.location / gauge.display_name / gauge.sort_name.

Runs the same agency-metadata resolver that build.py uses (NWRFC → NWPS →
USGS → reach.river → gauge-name heuristic), then applies a normalization
pass (`N Umpqua` → `North Umpqua`, comma-suffix fork styles flipped to
prefix, single-letter direction tokens expanded) so the displayed name is
consistent across agencies.

``sort_name`` encodes the full row order:
    {basin}|{fork_rank}{fork_label}|{elev_key}|{da_key}
so gauges.html's primary sort becomes plain alphabetical on ``sort_name``.
  - basin: river with any fork modifier stripped (``Umpqua`` for all
    N/S/mainstem rows in the Umpqua drainage)
  - fork_rank: ``0`` for fork rows, ``9`` for mainstem → forks sort first
  - fork_label: ``north`` / ``south`` / ``east`` / ``west`` / ``middle``
    (empty for mainstem) — distinguishes forks in the same basin
  - elev_key: ``10000 - elevation`` zero-padded so higher elevation sorts
    first (upstream ≈ higher); NULL → sentinel pushing row to end
  - da_key: drainage_area zero-padded so smaller catchment sorts first
    (upstream ≈ smaller); NULL → sentinel pushing row to end

Dry-run by default; pass ``--apply`` to write. Writes are idempotent — a
second run overwrites with identical values unless underlying metadata
changed.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

# Reuse the build pipeline's station-name parsers so we stay in lockstep with
# current behavior.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from kayak.db.safety import ProductionWriteRefused, refuse_configured_db, resolve_db_path
from kayak.web.build.gauges import (
    _parse_station_mixed,
    _parse_station_uppercase,
)


def _default_db() -> str | None:
    """Honor the legacy KAYAK_DB override, else the engine's configured DB — so the
    default ``--db`` is the same identity the prod-refuse interlock guards. Returns
    None if the configured URL carries no path (e.g. in-memory ``sqlite://``); wrapped
    so importing this script / ``--help`` never crashes on an odd ``DATABASE_URL``."""
    try:
        return os.environ.get("KAYAK_DB") or str(resolve_db_path(None))
    except ValueError:
        return None


DEFAULT_DB = _default_db()
DEFAULT_CACHE = "/home/pat/kayak/Gauge-metadata-cache/gauges.db"

_DIRECTIONS = ("North", "South", "East", "West", "Middle")
_DIRECTION_LETTERS = {
    "N": "North",
    "S": "South",
    "E": "East",
    "W": "West",
    "M": "Middle",
}
_FORK_ABBREVS = {
    "NF": "North Fork",
    "SF": "South Fork",
    "EF": "East Fork",
    "WF": "West Fork",
    "MF": "Middle Fork",
}
_DIR_ABBREV_DOT = {
    "N.": "North",
    "S.": "South",
    "E.": "East",
    "W.": "West",
    "M.": "Middle",
    "Mid": "Middle",
    "Mid.": "Middle",
}
# Tokens meaning "River" that agencies sometimes include but our display
# convention drops (we already strip a trailing " River" during parsing).
_RIVER_TOKENS = {"Rv", "Rv.", "R", "R."}
# Token-level replacements: abbreviation → expansion.
_TOKEN_EXPAND = {
    "Crk": "Creek",
    "Crk.": "Creek",
    "Fk": "Fork",
    "Fk.": "Fork",
}
# Parenthetical state disambiguators we strip from the tail of a river name
# (they're informative for a directory index, noisy for the display cell).
_PAREN_STATE_RE = re.compile(r"\s+\((?:OR|WA|ID|CA|NV|MT|WY|UT|AZ)\)\s*$", re.IGNORECASE)


def normalize_river(raw: str) -> str:
    """Harmonize fork/direction conventions across agencies.

    Canonical form: direction (full word) first, then ``Fork`` if applicable,
    then the base river name. Examples:

        ``N Umpqua``                   → ``North Umpqua``
        ``S UMPQUA``                   → ``South Umpqua`` (already title-cased)
        ``SF Coquille``                → ``South Fork Coquille``
        ``Alsea, N. Fork``             → ``North Fork Alsea``
        ``Applegate River, Middle Fork`` → ``Middle Fork Applegate``
        ``Hood, E. Fork``              → ``East Fork Hood``
        ``North Umpqua``               → ``North Umpqua`` (unchanged)
        ``Umpqua``                     → ``Umpqua`` (unchanged)
    """
    if not raw:
        return ""
    s = raw.strip()
    # Strip parenthetical state disambiguator ("Deschutes River (OR)" →
    # "Deschutes River") so it groups with cousin gauges that don't carry
    # the tag. The trailing " River" then gets stripped below.
    s = _PAREN_STATE_RE.sub("", s).strip()
    s = re.sub(r"\s+River$", "", s, flags=re.IGNORECASE)

    # 1. Comma-suffix pattern: "Base, X Fork" → "X Fork Base".
    # (Fork-of-fork connector collapse happens AFTER direction expansion so
    # "N FK OF M FK X" → "North Fork of Middle Fork X" first, then the "of"
    # gets dropped below.)
    m = re.match(r"^(?P<base>.+?),\s*(?P<tail>.+)$", s)
    if m:
        base = m.group("base").strip()
        tail = m.group("tail").strip()
        # Strip a trailing " River" from the base part (e.g. "Applegate River")
        base = re.sub(r"\s+River$", "", base, flags=re.IGNORECASE)
        # Normalize the tail (may be "N. Fork", "East Fork", "Middle Fork",
        # "East Fork of the South Fork", etc.) by running it back through our
        # directional substitutions below, then prepend it to the base.
        s = f"{_normalize_directions(tail)} {base}"
    else:
        s = _normalize_directions(s)
    # Collapse "Fork of (the) {Direction} Fork" → "Fork {Direction} Fork".
    # USGS spells their compound forks "N FK OF M FK WILLAMETTE"; the
    # paddler community writes them without the "of" ("North Fork Middle
    # Fork Willamette"), which is what we use for display.
    s = re.sub(
        rf"(Fork)\s+of\s+(?:the\s+)?(?=(?:{'|'.join(_DIRECTIONS)})\s+Fork)",
        r"\1 ",
        s,
        flags=re.IGNORECASE,
    )
    return _collapse_whitespace(s)


def _normalize_directions(s: str) -> str:
    """Expand abbreviated direction/fork tokens to full words in-place.

    Handles leading-token substitutions (so "N Umpqua" → "North Umpqua" but
    a word like "North" embedded deeper in a string is left alone), plus
    bare "Fk" → "Fork", and drops stray "Rv" / "R" river-abbreviation
    tokens we sometimes inherit from agency feeds.
    """
    tokens = s.split()
    if not tokens:
        return s
    changed: list[str] = []
    for i, tok in enumerate(tokens):
        t = tok
        # Drop "Rv" / "R." etc. (kept distinct from the leading-" RIVER "
        # strip done higher up — this handles embedded mid-string uses).
        if t in _RIVER_TOKENS:
            continue
        up = t.upper().replace(".", "")
        # XF abbreviations: "SF" → "South Fork" (also "SF." and "S.F."). In
        # river names these tokens are always fork abbreviations, so expand
        # regardless of position; we only suppress the expansion when it's
        # trailing a token that couldn't grammatically be followed by a fork
        # (none apply in our dataset).
        if up in _FORK_ABBREVS:
            changed.append(_FORK_ABBREVS[up])
            continue
        # Token-level expansions (Fk → Fork, Crk → Creek, etc.)
        if t in _TOKEN_EXPAND:
            changed.append(_TOKEN_EXPAND[t])
            continue
        # Dotted letter: "N." → "North"
        if t in _DIR_ABBREV_DOT:
            changed.append(_DIR_ABBREV_DOT[t])
            continue
        # Bare single letter: "N" → "North" (only if followed by more tokens
        # so we don't clobber e.g. a literal one-letter location)
        if len(t) == 1 and t.upper() in _DIRECTION_LETTERS and i < len(tokens) - 1:
            changed.append(_DIRECTION_LETTERS[t.upper()])
            continue
        changed.append(t)
    return " ".join(changed)


def _collapse_whitespace(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def basin_and_fork(river: str) -> tuple[str, str]:
    """Split a normalized river name into (basin, fork_label).

    Iteratively peels off ``{direction} [Fork]`` and ``of [the] {direction}
    [Fork]`` prefixes until what remains is the base river; the *first*
    direction seen is returned as the primary fork label (so
    ``North Fork of Middle Fork Willamette`` → basin ``Willamette``, fork
    ``north``, which still groups with other Willamette tributaries).

        ``North Fork Alsea``                 → (``Alsea``,      ``north``)
        ``North Umpqua``                     → (``Umpqua``,     ``north``)
        ``Hood``                             → (``Hood``,       ``"")``   # mainstem
        ``East Fork of South Fork Salmon``   → (``Salmon``,     ``east``)
        ``North Fork of Middle Fork Willamette`` → (``Willamette``, ``north``)
    """
    if not river:
        return "", ""
    dir_re = "|".join(_DIRECTIONS)
    peel_re = re.compile(
        rf"^(?:of\s+(?:the\s+)?)?(?P<dir>{dir_re})(?:\s+Fork)?\s+",
        re.IGNORECASE,
    )
    s = river
    dirs_seen: list[str] = []
    while True:
        m = peel_re.match(s)
        if not m:
            break
        dirs_seen.append(m.group("dir").lower())
        s = s[m.end() :]
    if dirs_seen and s.strip():
        return s.strip(), dirs_seen[0]
    return river, ""


def build_sort_name(river: str, elevation: float | None, drainage_area: float | None) -> str:
    """Compose the single alphabetical key described in the module docstring."""
    basin, fork = basin_and_fork(river or "")
    fork_rank = "0" if fork else "9"
    # Elevation DESC: invert so higher → smaller numeric. Sentinel 15000 for
    # NULL pushes rows without elevation to the end of their group.
    if elevation is not None:
        elev_key = f"{round(10000 - float(elevation)):06d}"
    else:
        elev_key = "999999"
    # DA ASC: zero-pad. Sentinel 999999 pushes NULL DA to end.
    if drainage_area is not None:
        da_key = f"{round(float(drainage_area)):06d}"
    else:
        da_key = "999999"
    return f"{basin.lower()}|{fork_rank}{fork}|{elev_key}|{da_key}"


def build_display_name(river: str, location: str) -> str:
    """Simple ``{river} at {location}`` — or just the river if no location.

    Keeps it uniform; agencies' natural styles ("near X", "below Y") are
    not preserved here on purpose — the location cell carries the nuance.
    """
    river = (river or "").strip()
    location = (location or "").strip()
    if river and location:
        return f"{river} at {location}"
    return river or location


def load_metadata_cache(cache_path: str) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {"nwrfc": {}, "nwps": {}, "usgs": {}}
    if not Path(cache_path).exists():
        return out
    conn = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
    try:
        for kind, query in (
            ("nwrfc", "SELECT lid, name FROM nwrfc_site WHERE name IS NOT NULL"),
            ("nwps", "SELECT lid, name FROM nwps_site WHERE name IS NOT NULL"),
            ("usgs", "SELECT site_no, station_nm FROM usgs_site WHERE station_nm IS NOT NULL"),
        ):
            for key, name in conn.execute(query):
                out[kind][key] = name
    finally:
        conn.close()
    return out


_SUFFIX_STRIP_RE = re.compile(r"_(calc|merge|merged|calculation)$", re.IGNORECASE)


def _river_from_gauge_name_smart(name: str) -> str:
    """Smarter heuristic than build.py's: preserves fork prefixes.

    ``NF_ROGUE_LOST_CREEK_calc`` → ``NF Rogue`` (before normalization then
    expands to ``North Fork Rogue``). ``Clackamas_Three_Lynx_merge`` →
    ``Clackamas``. Plain numeric USGS IDs pass through unchanged.
    """
    if not name:
        return ""
    stripped = _SUFFIX_STRIP_RE.sub("", name)
    parts = [p for p in stripped.split("_") if p]
    if not parts:
        return name
    first = parts[0]
    if first.isdigit():
        return name
    # Fork abbreviation or single-letter direction at the front → pull the
    # next segment as the basin so the normalizer can produce
    # ``North Fork Rogue`` or ``North Umpqua`` rather than just ``North``.
    up = first.upper().replace(".", "")
    is_fork = up in _FORK_ABBREVS
    is_dir = len(first) == 1 and first.upper() in _DIRECTION_LETTERS
    if (is_fork or is_dir) and len(parts) >= 2:
        # Title-case the basin segment so uppercase names like "ALSEA" land
        # as "Alsea" before downstream normalization picks them up.
        return f"{first} {parts[1].title()}"
    return first.replace("-", " ")


def resolve_raw(
    gauge: sqlite3.Row, metadata: dict[str, dict[str, str]], reach_river: str | None
) -> tuple[str, str, str]:
    """Mirror _resolve_river_location; also return the provenance string."""
    if gauge["nwsli_id"]:
        name = metadata["nwrfc"].get(gauge["nwsli_id"]) or metadata["nwps"].get(gauge["nwsli_id"])
        if name:
            river, location = _parse_station_mixed(name)
            return river, location, "nwrfc/nwps"
    if gauge["usgs_id"]:
        name = metadata["usgs"].get(gauge["usgs_id"])
        if name:
            river, location = _parse_station_uppercase(name)
            return river, location, "usgs"
    if reach_river:
        return reach_river, gauge["location"] or "", "reach.river"
    return _river_from_gauge_name_smart(gauge["name"]), gauge["location"] or "", "heuristic"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--allow-production",
        action="store_true",
        help="Override the production-DB refusal and write the configured DB directly "
        "(gauge display columns are dataset-owned; normally write a scratch/dev copy "
        "and export_metadata the result).",
    )
    ap.add_argument("--limit", type=int, default=0, help="Show only the first N rows in preview.")
    args = ap.parse_args()

    # gauge.river/location/display_name/sort_name are dataset-owned — refuse to
    # mutate the configured production DB directly (SA / AC #6). Fail fast: write a
    # scratch/dev copy and export_metadata the result, or pass --allow-production.
    if args.apply:
        try:
            refuse_configured_db(args.db, allow_production=args.allow_production)
        except ProductionWriteRefused as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    metadata = load_metadata_cache(args.cache)

    gauges = conn.execute(
        """
        SELECT id, name, usgs_id, nws_id, nwsli_id,
               location, elevation, drainage_area
        FROM gauge
        ORDER BY id
        """
    ).fetchall()

    # Pre-fetch reach.river for each gauge so we don't hit the DB per-row.
    reach_river_map: dict[int, str] = {}
    for row in conn.execute(
        "SELECT gauge_id, river FROM reach WHERE gauge_id IS NOT NULL AND river IS NOT NULL"
    ):
        reach_river_map.setdefault(row["gauge_id"], row["river"])

    rows = []
    for g in gauges:
        raw_river, raw_loc, provenance = resolve_raw(g, metadata, reach_river_map.get(g["id"]))
        river = normalize_river(raw_river)
        location = (raw_loc or "").strip()
        display_name = build_display_name(river, location)
        sort_name = build_sort_name(river, g["elevation"], g["drainage_area"])
        rows.append(
            {
                "id": g["id"],
                "name": g["name"],
                "provenance": provenance,
                "river": river,
                "location": location,
                "display_name": display_name,
                "sort_name": sort_name,
            }
        )

    # Preview, grouped by basin so the Umpqua/Santiam/Hood clustering is
    # easy to eyeball. Sort by sort_name to show the final row order.
    rows_sorted = sorted(rows, key=lambda r: r["sort_name"])
    preview = rows_sorted if args.limit == 0 else rows_sorted[: args.limit]
    hdr = f"{'id':>4}  {'prov':<11}  {'river':<28}  {'location':<22}  {'sort_name'}"
    print(hdr)
    print("-" * 120)
    for r in preview:
        print(
            f"{r['id']:>4}  {r['provenance']:<11}  "
            f"{(r['river'] or '')[:28]:<28}  {(r['location'] or '')[:22]:<22}  "
            f"{r['sort_name']}"
        )

    print(f"\n{len(rows)} gauges processed.")

    if not args.apply:
        print("Dry-run only. Pass --apply to write changes.")
        return 0

    cur = conn.cursor()
    cur.executemany(
        """
        UPDATE gauge
        SET river = ?, location = ?, display_name = ?, sort_name = ?
        WHERE id = ?
        """,
        [(r["river"], r["location"], r["display_name"], r["sort_name"], r["id"]) for r in rows],
    )
    conn.commit()
    print(f"Applied {cur.rowcount} update(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
