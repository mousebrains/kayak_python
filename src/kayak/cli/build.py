"""Builder command — generates static HTML/CSV/text files to disk.

Writes complete, self-contained HTML pages with inlined CSS to an output
directory (default: public_html/).  Each page has responsive mobile-first
styling, state navigation links, and inline SVG sparklines.
"""

import argparse
import filecmp
import hashlib
import html as html_mod
import json
import logging
import math
import os
import re
import shutil
import sqlite3
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from kayak.config import BASE_DIR, SITE_URL
from kayak.db.cache import get_all_latest_gauges
from kayak.db.engine import get_session
from kayak.db.gauges import get_calculated_gauge_ids
from kayak.db.models import DataType, Gauge, HucName, LatestGaugeObservation, Reach
from kayak.db.reaches import all_state_names, reaches_query
from kayak.utils.class_tiers import parse_class_tiers
from kayak.utils.simplify import parse_geom, simplify

# Phase 1 of the build.py split (docs/PLAN_build_split.md): the constants
# and small helpers below were moved to kayak.web.build._shared. Re-import
# them here so the un-moved code in this file (and external consumers that
# patch `kayak.cli.build.<name>`) keep resolving the same objects. These
# re-exports go away when Phase 8 replaces this file with the slim shim.
from kayak.web.build._shared import (
    _ABBR_TO_STATE,
    _CSS_PATH,
    _FILTERS_JS_PATH,
    _JS_PATH,
    _NAV_STATES,
    DATA_EXPIRY_THRESHOLD,
    DATA_STALE_THRESHOLD,
    _atomic_write,
    _css_link_tag,
    _load_css,
)
from kayak.web.build.exports import _build_csv, _build_text
from kayak.web.build.levels import (
    _build_filter_bar,
    _build_html_table,
    _collect_filter_data,
    _get_builder_columns,
    _get_row_data,
)
from kayak.web.build.shell import (
    _build_map_page,
    _build_page,
    _build_placeholder_page,
)
from kayak.web.build.sparklines import (
    _build_sparkline,
    _select_sparkline_series,
    _sparkline_svg_from_records,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — extracted from inline magic numbers
# ---------------------------------------------------------------------------

# GeoJSON geometry simplification. Coordinate precision is matched to the
# simplify epsilon - quantizing below the simplification grid would be wasted
# bytes. At 44N, 1e-5 deg ~= 0.8-1.1 m (below NHD's horizontal accuracy);
# 3e-4 deg ~= 24-33 m, which keeps polygonalization invisible up to ~zoom 14.
GEOJSON_SIMPLIFY_EPSILON = 0.0003
GEOJSON_COORD_PRECISION = 5
assert math.ceil(-math.log10(GEOJSON_SIMPLIFY_EPSILON)) + 1 <= GEOJSON_COORD_PRECISION


# ---------------------------------------------------------------------------
# GeoJSON
# ---------------------------------------------------------------------------


def _reach_geometry(reach: Reach, epsilon: float) -> dict | None:
    """Return a GeoJSON geometry dict for *reach* (simplified + rounded) or None.

    Falls back from WKT ``geom`` → start/end lat-lon pair → single lat-lon point.
    """
    p = GEOJSON_COORD_PRECISION
    if reach.geom:
        points = parse_geom(reach.geom)
        if len(points) >= 2:
            simplified = simplify(points, epsilon)
            return {
                "type": "LineString",
                "coordinates": [[round(x, p), round(y, p)] for x, y in simplified],
            }
        if len(points) == 1:
            pt = points[0]
            return {"type": "Point", "coordinates": [round(pt[0], p), round(pt[1], p)]}
    if (
        reach.latitude_start is not None
        and reach.longitude_start is not None
        and reach.latitude_end is not None
        and reach.longitude_end is not None
    ):
        return {
            "type": "LineString",
            "coordinates": [
                [round(float(reach.longitude_start), p), round(float(reach.latitude_start), p)],
                [round(float(reach.longitude_end), p), round(float(reach.latitude_end), p)],
            ],
        }
    if reach.latitude is not None and reach.longitude is not None:
        return {
            "type": "Point",
            "coordinates": [round(float(reach.longitude), p), round(float(reach.latitude), p)],
        }
    return None


def _build_reaches_static(
    reaches: list[Reach],
    epsilon: float = GEOJSON_SIMPLIFY_EPSILON,
) -> str:
    """Static per-reach geometry + metadata.

    Changes only when a reach is edited or retraced, so this file is
    long-cached by the browser (the hourly rebuild produces identical
    bytes most of the time).
    """
    features: list[dict] = []
    for reach in reaches:
        geometry = _reach_geometry(reach, epsilon)
        if geometry is None:
            continue
        tiers: set[str] = set()
        for c in reach.classes:
            tiers.update(parse_class_tiers(c.name))
        ordered_tiers = sorted(tiers, key=lambda t: ("I", "II", "III", "IV", "V").index(t))
        props = {
            "id": reach.id,
            "name": reach.display_name or reach.name or "",
            "tiers": ordered_tiers or ["?"],
            "state": reach.states[0].name if reach.states else "",
        }
        features.append({"type": "Feature", "properties": props, "geometry": geometry})
    return json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":"))


_POPUP_PRIMARY_ORDER: tuple[tuple[DataType, str, str], ...] = (
    (DataType.flow, "flow", "cfs"),
    (DataType.inflow, "flow", "cfs"),
    (DataType.gauge, "gage", "ft"),
)


def _build_reaches_state(
    reaches: list[Reach],
    calculated_gauge_ids: set[int],
    all_latest: dict[tuple[int, DataType], LatestGaugeObservation],
) -> str:
    """Per-reach popup data — the bit that changes every build.

    Per-reach entry keys (all but ``s`` optional):
      s   status: low|okay|high|unknown
      t   primary data-type label: "flow" or "gage"
      v   primary numeric value (int for flow, 2-dp float for gage)
      u   unit short form: "cfs" or "ft"
      d   delta_per_hour (omitted when null)
      ts  observed_at as UTC ISO ending in "Z"

    Temperature is intentionally skipped; popup priority is flow > inflow
    (rendered as flow) > gage. Status comes from ``_get_row_data`` so it
    matches the listing pages even when the level threshold lives on a
    different data type than the displayed value.
    """
    out: dict[str, dict] = {}
    for reach in reaches:
        # Only emit reaches whose geometry also makes it into the static
        # file; otherwise the client would carry state it cannot paint.
        if _reach_geometry(reach, GEOJSON_SIMPLIFY_EPSILON) is None:
            continue
        row = _get_row_data(reach, calculated_gauge_ids, all_latest)
        entry: dict = {"s": row.get("status", "unknown")}

        gauge = reach.gauge
        if gauge is not None:
            for dtype, label, unit in _POPUP_PRIMARY_ORDER:
                cand = all_latest.get((gauge.id, dtype))
                if cand is None or cand.value is None:
                    continue
                entry["t"] = label
                if label == "flow":
                    entry["v"] = round(float(cand.value))
                else:
                    entry["v"] = round(float(cand.value), 2)
                entry["u"] = unit
                if cand.delta_per_hour is not None:
                    entry["d"] = round(float(cand.delta_per_hour), 2)
                if cand.observed_at is not None:
                    obs = cand.observed_at
                    obs = obs.replace(tzinfo=UTC) if obs.tzinfo is None else obs.astimezone(UTC)
                    entry["ts"] = obs.isoformat().replace("+00:00", "Z")
                break

        out[str(reach.id)] = entry
    return json.dumps(out, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Gauges page — supplemental all-gauges listing
# ---------------------------------------------------------------------------


_METADATA_CACHE_PATH = BASE_DIR / "Gauge-metadata-cache" / "gauges.db"

# Trailing state code — ", OR" / ",OR" / " OR" / " OREG" / ", OREG.". Explicit
# list avoids false-matching any uppercase 2-letter token ("N JUNCTION", etc.).
_STATE_SUFFIX_RE = re.compile(
    r",?\s*(?:OR|OREG\.?|WA|WASH\.?|ID|IDA\.?|CA|CAL\.?|NV|NEV\.?"
    r"|MT|MONT\.?|WY|WYO\.?|UT|AZ|ARIZ\.?|CO|COL\.?|NM|KS|TX|AK|HI)\s*$"
)

# Fork-prefix abbreviations the user wants kept in all-caps.
_KEEP_CAPS_TOKENS = {"EF", "NF", "SF", "MF", "WF"}

# Lowercase connector words inside a multi-word fragment.
_SMALL_WORDS = {"of", "the", "at", "in", "on", "to", "and", "or"}


def _load_station_metadata() -> dict[str, dict[str, str]]:
    """Read descriptive station names from the Gauge-metadata-cache.

    Returns a dict with three sub-dicts keyed by ``lid`` / ``site_no``:
    ``nwrfc`` (mixed case), ``nwps`` (mixed case), ``usgs`` (UPPERCASE).
    An empty set of dicts is returned if the cache file is missing or
    unreadable — callers fall back to the current name-derivation logic.
    """
    out: dict[str, dict[str, str]] = {"nwrfc": {}, "nwps": {}, "usgs": {}}
    if not _METADATA_CACHE_PATH.is_file():
        return out
    try:
        conn = sqlite3.connect(f"file:{_METADATA_CACHE_PATH}?mode=ro", uri=True)
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
    except sqlite3.Error as exc:
        logger.warning("metadata cache unreadable at %s: %s", _METADATA_CACHE_PATH, exc)
    return out


def _title_case_usgs(s: str) -> str:
    """Title-case an UPPERCASE USGS fragment.

    - ``NR`` / ``NEAR`` → lowercase (per user convention in location strings)
    - ``EF`` / ``NF`` / ``SF`` / ``MF`` / ``WF`` → kept uppercase
    - Connector words (``of``, ``the`` ...) lowercased when not leading
    - Other words capitalized (``CRK`` → ``Crk``)
    """
    words = s.split()
    out: list[str] = []
    for i, w in enumerate(words):
        low = w.lower()
        upper = w.upper()
        if low in ("nr", "near"):
            out.append(low)
        elif upper in _KEEP_CAPS_TOKENS:
            out.append(upper)
        elif i > 0 and low in _SMALL_WORDS:
            out.append(low)
        else:
            out.append(w.capitalize())
    return " ".join(out)


def _parse_station_uppercase(name: str) -> tuple[str, str]:
    """Parse a USGS-style UPPERCASE station name to ``(river, location)``.

    ``WILLAMETTE RIVER AT CORVALLIS, OR`` → ``("Willamette", "Corvallis")``
    ``SHITIKE CRK AT PETERS PASTURE, NR WARM SPRINGS, OR``
        → ``("Shitike Crk", "Peters Pasture, nr Warm Springs")``
    """
    s = _STATE_SUFFIX_RE.sub("", name.strip())
    # USGS primary delimiters: AT, NEAR, NR, BLW/BELOW, ABV/ABOVE/AB, and
    # the stray single-letter "A" variant ("KLAMATH R A ORLEANS"). maxsplit=1
    # keeps a secondary "NR" ("AT PETERS PASTURE, NR WARM SPRINGS") inside
    # the location; longer alternatives are listed first so "ABOVE" wins
    # over "AB" (and "AT" over "A") when both could match the same position.
    parts = re.split(r"\s+(?:ABOVE|BELOW|NEAR|ABV|BLW|AB|AT|NR|A)\s+", s, maxsplit=1)
    if len(parts) != 2:
        return _title_case_usgs(s), ""
    left, right = parts
    # Strip trailing " RIVER" or its USGS abbreviation " R".
    left = re.sub(r"\s+R(?:IVER)?$", "", left)
    return _title_case_usgs(left), _title_case_usgs(right)


def _parse_station_mixed(name: str) -> tuple[str, str]:
    """Parse a mixed-case NWPS/NWRFC station name to ``(river, location)``.

    Input is already in presentation case; we don't re-title-case. Splits on
    ``at``/``near``/``above``/``below`` and strips a trailing `` River``
    suffix from the river. Also collapses the NWRFC-textplot Unicode-minus
    delimiter (``'WILLAMETTE <U+2212> AT CORVALLIS'``) before splitting.
    """
    # Collapse the NWRFC-textplot dashes so that "X <dash> AT Y" splits on " AT ".
    # Dashes matched: minus (U+2212), en-dash (U+2013), em-dash (U+2014), ASCII hyphen.
    s = re.sub("\\s*[−–—-]\\s*", " ", name.strip())  # noqa: RUF001
    parts = re.split(r"\s+(?:at|near|above|below)\s+", s, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2:
        return s, ""
    left, right = parts[0].strip(), parts[1].strip()
    left = re.sub(r"\s+river$", "", left, flags=re.IGNORECASE)
    return left, right


def _resolve_river_location(
    gauge: Gauge,
    metadata: dict[str, dict[str, str]],
    reach_river: str,
) -> tuple[str, str]:
    """Resolve (river, location) for one gauge with layered fallbacks.

    Priority: NWRFC → NWPS → USGS → linked-reach river + gauge.location →
    gauge-name heuristic.
    """
    if gauge.nwsli_id:
        name = metadata["nwrfc"].get(gauge.nwsli_id) or metadata["nwps"].get(gauge.nwsli_id)
        if name:
            return _parse_station_mixed(name)
    if gauge.usgs_id:
        name = metadata["usgs"].get(gauge.usgs_id)
        if name:
            return _parse_station_uppercase(name)
    if reach_river:
        return reach_river, gauge.location or ""
    return _river_from_gauge_name(gauge.name), gauge.location or ""


def _river_from_gauge_name(name: str) -> str:
    """Best-effort river name from a gauge's canonical name.

    Fallback used when no linked reach has ``reach.river`` set. Pattern
    ``River_Location_merge`` → ``River``; numeric USGS IDs pass through
    unchanged.
    """
    if not name:
        return ""
    if "_" in name:
        head = name.split("_", 1)[0]
        if head and not head.isdigit():
            return head.replace("-", " ")
    return name


def _gauge_status_from_reaches(
    reaches: list[Reach],
    calculated_gauge_ids: set[int],
    all_latest: dict[tuple[int, DataType], LatestGaugeObservation],
) -> tuple[str | None, dict[str, int]]:
    """Roll up the per-reach status of a gauge's associated reaches into one
    "anything runnable?" verdict for the gauge listing.

    Rule: 'okay' if any reach is currently okay; otherwise the more common
    of 'low'/'high' (ties go to 'low'). Returns (None, counts) when no
    associated reach has a defined status — caller emits no data-status.
    """
    counts = {"low": 0, "okay": 0, "high": 0}
    for r in reaches:
        s = _get_row_data(r, calculated_gauge_ids, all_latest).get("status")
        if s in counts:
            counts[s] += 1
    if counts["okay"]:
        return "okay", counts
    if counts["low"] == 0 and counts["high"] == 0:
        return None, counts
    return ("low" if counts["low"] >= counts["high"] else "high"), counts


def _collect_gauge_rows(
    session: Session,
    all_latest: dict[tuple[int, DataType], LatestGaugeObservation],
    metadata: dict[str, dict[str, str]],
    calculated_gauge_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Build one row per gauge with at least one current observation.

    Excludes expired (>7d stale) gauges, mirroring the index page rule. Each
    row carries flow/gage/temperature/time plus state and HUC sets derived
    from any reaches that reference the gauge (used for filter pills).
    """
    gauge_ids_with_data = {gid for gid, _ in all_latest}
    if not gauge_ids_with_data:
        return []
    calc_ids = calculated_gauge_ids or set()

    gauges = list(session.scalars(select(Gauge).where(Gauge.id.in_(gauge_ids_with_data))))
    reach_rows = list(session.scalars(select(Reach).where(Reach.gauge_id.in_(gauge_ids_with_data))))
    gauge_reaches: dict[int, list[Reach]] = {}
    for r in reach_rows:
        if r.gauge_id:
            gauge_reaches.setdefault(r.gauge_id, []).append(r)

    rows: list[dict[str, Any]] = []
    for g in gauges:
        reaches = gauge_reaches.get(g.id, [])
        # Prefer the pre-normalized columns populated by
        # scripts/seed_gauge_display.py. The resolver-based fallback only
        # fires for brand-new rows inserted after the last seeder run.
        if g.river is not None:
            river = g.river
            location = g.location or ""
            display_name = g.display_name or river
            sort_name = g.sort_name or river.lower()
        else:
            reach_river = next((r.river for r in reaches if r.river), "")
            river, location = _resolve_river_location(g, metadata, reach_river)
            display_name = f"{river} at {location}" if river and location else river or location
            # Best-effort key so unseeded rows still land in a sensible slot.
            elev = float(g.elevation) if g.elevation is not None else None
            elev_key = f"{round(10000 - elev):06d}" if elev is not None else "999999"
            sort_name = f"{river.lower()}|9|{elev_key}|999999"

        row: dict[str, Any] = {
            "gauge_id": g.id,
            "river": river,
            "location": location,
            "display_name": display_name,
            "sort_name": sort_name,
            "is_estimated": g.id in calc_ids,
        }

        for dtype_name, dtype in [
            ("flow", DataType.flow),
            ("gage", DataType.gauge),
            ("temperature", DataType.temperature),
            ("inflow", DataType.inflow),
        ]:
            latest = all_latest.get((g.id, dtype))
            if latest is None or latest.value is None:
                continue
            if dtype_name == "inflow":
                if "flow" in row:
                    continue
                row["flow"] = latest.value
            else:
                row[dtype_name] = latest.value
            if "time" not in row or latest.observed_at > row["time"]:
                row["time"] = latest.observed_at

        if not any(k in row for k in ("flow", "gage", "temperature")):
            continue

        obs_time = row.get("time")
        if isinstance(obs_time, datetime):
            if obs_time.tzinfo is None:
                obs_time = obs_time.replace(tzinfo=UTC)
            age = datetime.now(UTC) - obs_time
            if age > DATA_EXPIRY_THRESHOLD:
                continue
            if age > DATA_STALE_THRESHOLD:
                row["stale"] = True

        # Filter pills come straight from the gauge row — gauges.html no
        # longer walks linked reaches for state/HUC. data-state on the row is
        # the full state name (matches reach-side convention); the table cell
        # still shows the postal abbreviation.
        state_abbrev = g.state or ""
        state_name = _ABBR_TO_STATE.get(state_abbrev, "")
        gauge_huc = g.huc or ""
        row["state"] = state_name
        row["state_abbrev"] = state_abbrev
        row["huc6"] = gauge_huc[:6] if len(gauge_huc) >= 6 else ""
        row["huc8"] = gauge_huc[:8] if len(gauge_huc) >= 8 else ""
        row["has_huc"] = bool(row["huc8"])
        row["drainage_area"] = float(g.drainage_area) if g.drainage_area is not None else None
        row["elevation"] = float(g.elevation) if g.elevation is not None else None

        # "Anything runnable?" rollup of associated-reach statuses.
        status, status_counts = _gauge_status_from_reaches(reaches, calc_ids, all_latest)
        if status is not None:
            row["status"] = status
        row["status_counts"] = status_counts

        rows.append(row)

    # sort_name encodes the full row order (basin → fork rank → elevation
    # DESC → DA ASC) as a single alphabetical key, so the sort is a plain
    # lexicographic comparison on the pre-computed string. See
    # scripts/seed_gauge_display.py for how the key is assembled.
    rows.sort(key=lambda r: (r["sort_name"], r["gauge_id"]))
    return rows


def _build_gauges_table(rows: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """Render the gauges <table>; returns (html, first-letter list for nav)."""
    lines: list[str] = []
    lines.append('<table class="levels">')
    lines.append("<thead><tr>")
    lines.append('  <th scope="col">Status</th>')
    lines.append('  <th scope="col">River</th>')
    lines.append('  <th scope="col">Location</th>')
    lines.append('  <th scope="col">Date</th>')
    lines.append('  <th scope="col">Flow<br>cfs</th>')
    lines.append('  <th scope="col" class="secondary">2-day Trend</th>')
    lines.append('  <th scope="col">Gauge<br>ft</th>')
    lines.append('  <th scope="col">Temp<br>&deg;F</th>')
    lines.append("</tr></thead>")
    lines.append("<tbody>")

    prev_letter = ""
    letters: list[str] = []
    for row in rows:
        gid = row["gauge_id"]
        river = row["river"]
        location = row["location"]
        # Letter nav follows the basin (first segment of sort_name) so that
        # e.g. all Umpqua-family rows — North Umpqua, South Umpqua, Umpqua
        # — share the letter "U" the way they share the table group.
        basin_key = row["sort_name"].split("|", 1)[0] if row.get("sort_name") else ""
        cur_letter = (basin_key or river or location or "")[:1].upper()
        letter_id = ""
        if cur_letter and cur_letter != prev_letter:
            letter_id = f' id="letter-{cur_letter}"'
            letters.append(cur_letter)
            prev_letter = cur_letter

        state = row["state"]
        huc8 = row["huc8"]

        stale = " stale" if row.get("stale") else ""
        # Emit filter attrs only when state+huc8 are both populated. A partial
        # set would match the filters.js selector `tr[data-state],...` but
        # then fail match() in any group whose attr is empty, hiding the row
        # permanently. All-or-nothing keeps orphan gauges visible. data-status
        # rides along on the same condition so unrolled-up gauges (status word
        # is None) still match the "unknown" pill, while pure orphans stay
        # out of the filter set entirely.
        status_word = row.get("status")
        if state and huc8:
            status_for_attr = status_word or "unknown"
            attrs = (
                f' data-state="{html_mod.escape(state)}"'
                f' data-huc8="{html_mod.escape(huc8)}"'
                f' data-status="{status_for_attr}"'
            )
        else:
            attrs = ""
        lines.append(
            f'<tr{letter_id} class="clickable-row{stale}" data-href="/gauge.php?id={gid}"{attrs}>'
        )

        if status_word:
            counts = row.get("status_counts") or {}
            count_summary = ", ".join(f"{n} {lvl}" for lvl, n in counts.items() if n)
            title = f' title="{count_summary}"' if count_summary else ""
            status_cell = f'<span class="level-{status_word}"{title}>{status_word}</span>'
        else:
            status_cell = ""
        lines.append(f'  <td class="td-status" data-label="Status">{status_cell}</td>')

        est = '<span class="est"> (est)</span>' if row.get("is_estimated") else ""
        lines.append(
            f'  <td class="td-name" data-label="River">'
            f'<a href="/gauge.php?id={gid}">{html_mod.escape(river)}{est}</a></td>'
        )
        lines.append(f'  <td data-label="Location">{html_mod.escape(location)}</td>')

        time_val = row.get("time")
        if isinstance(time_val, datetime):
            iso = time_val.strftime("%Y-%m-%dT%H:%M:%SZ")
            disp = time_val.strftime("%m/%d %H:%M")
            date_cell = f'<time datetime="{iso}">{disp}</time>'
        else:
            date_cell = ""
        lines.append(f'  <td class="td-date" data-label="Date">{date_cell}</td>')

        flow_val = row.get("flow")
        gage_val = row.get("gage")
        if isinstance(flow_val, int | float):
            flow_cell = f"{flow_val:,.0f}"
        elif isinstance(gage_val, int | float):
            flow_cell = f"{gage_val:,.1f}&prime;"
        else:
            flow_cell = ""
        lines.append(f'  <td class="td-flow" data-label="Flow">{flow_cell}</td>')

        lines.append(
            f'  <td class="td-spark secondary" data-label="2-day Trend">'
            f'<span class="spark" data-gid="{gid}"></span></td>'
        )

        gage_val = row.get("gage")
        gage_cell = f"{gage_val:,.1f}" if isinstance(gage_val, int | float) else ""
        lines.append(f'  <td class="td-gage" data-label="Gauge">{gage_cell}</td>')

        temp_val = row.get("temperature")
        temp_cell = f"{temp_val:.1f}" if isinstance(temp_val, int | float) else ""
        lines.append(f'  <td class="td-temp" data-label="Temp">{temp_cell}</td>')
        lines.append("</tr>")

    lines.append("</tbody></table>")
    return "\n".join(lines), letters


def _build_gauges_filter_bar(
    rows: list[dict[str, Any]],
    huc6_names: dict[str, str],
    huc8_names: dict[str, str],
) -> str:
    """Filter bar for gauges page: State + Watershed + Status (no class tier).

    Reads ``state``/``huc6``/``huc8``/``has_huc``/``status`` directly from
    each row. Status comes from the rolled-up reach statuses — gauges with
    no associated reach (or no flow thresholds) carry no data-status, which
    filters.js treats as the empty value, so we expose an "unknown" pill.
    """
    states: set[str] = set()
    huc6_to_huc8s: dict[str, set[tuple[str, str]]] = {}
    has_no_huc = False
    statuses: set[str] = set()
    for r in rows:
        if r["state"]:
            states.add(r["state"])
        if r["has_huc"]:
            huc6_to_huc8s.setdefault(r["huc6"], set()).add(
                (r["huc8"], huc8_names.get(r["huc8"], r["huc8"]))
            )
        else:
            has_no_huc = True
        # Only filterable rows (state + huc8 present) carry data-status, so
        # only those should contribute pill values. Otherwise the "unknown"
        # pill could appear without anything for it to match.
        if r["state"] and r["has_huc"]:
            statuses.add(r.get("status") or "unknown")
    huc6_groups = [
        {
            "huc6": huc6,
            "name": huc6_names.get(huc6, huc6),
            "huc8s": sorted(huc8s),
        }
        for huc6, huc8s in sorted(
            huc6_to_huc8s.items(), key=lambda kv: huc6_names.get(kv[0], kv[0])
        )
    ]
    filter_data = {
        "state": sorted(states),
        "huc6_groups": huc6_groups,
        "has_no_huc": has_no_huc,
        "status": [s for s in ("low", "okay", "high", "unknown") if s in statuses],
        "tier": [],
    }
    return _build_filter_bar(filter_data, is_all_page=True)


def _write_gauges_page(
    session: Session,
    all_latest: dict[tuple[int, DataType], LatestGaugeObservation],
    states: list[str],
    css_link: str,
    output_dir: Path,
) -> None:
    """Render gauges.html and ensure sparklines.json covers every shown gauge."""
    metadata = _load_station_metadata()
    gauge_ids_with_data = list({gid for gid, _ in all_latest})
    calc_ids = get_calculated_gauge_ids(session, gauge_ids_with_data)
    rows = _collect_gauge_rows(session, all_latest, metadata, calc_ids)
    logger.info("Building gauges.html: %d gauges", len(rows))
    print(f"Building gauges.html: {len(rows)} gauges")

    table_html, letters = _build_gauges_table(rows)
    huc6_names: dict[str, str] = {
        r.code: r.name for r in session.scalars(select(HucName).where(HucName.level == 6))
    }
    huc8_names: dict[str, str] = {
        r.code: r.name for r in session.scalars(select(HucName).where(HucName.level == 8))
    }
    filter_bar_html = _build_gauges_filter_bar(rows, huc6_names, huc8_names)
    page_html = _build_page(
        table_html,
        css_link,
        states,
        current_state="",
        title="River Gauges",
        letters=letters,
        filter_bar_html=filter_bar_html,
        active_page="gauges",
        picker_kind="gauge",
        path="/gauges.html",
    )
    _atomic_write(output_dir / "gauges.html", page_html)

    # Merge sparklines for any gauges the index build didn't already cover.
    sparklines_path = output_dir / "static" / "sparklines.json"
    try:
        existing: dict[str, str] = json.loads(sparklines_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}
    missing = [row["gauge_id"] for row in rows if str(row["gauge_id"]) not in existing]
    if missing:
        extra_obs = _select_sparkline_series(session, missing)
        for gid, records in extra_obs.items():
            svg = _sparkline_svg_from_records(records)
            if svg:
                existing[str(gid)] = svg
        sparklines_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(sparklines_path, json.dumps(existing))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def addArgs(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the 'build' subcommand."""
    parser = subparsers.add_parser(
        "build", help="Generate static HTML/CSV/text files to output directory"
    )
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("OUTPUT_DIR", str(BASE_DIR / "public_html")),
        help="Output directory (default: $OUTPUT_DIR or public_html/)",
    )
    parser.set_defaults(func=build)


def _deploy_source_files(output_dir: Path) -> None:
    """Copy source files from the repo into the output directory.

    Makes the output directory self-contained — no symlinks pointing
    back into the repo.  Covers static assets, PHP files, and config.
    """
    # Static assets (icons, JS, manifest)
    static_dir = output_dir / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    src_static = BASE_DIR / "static"
    for path in src_static.iterdir():
        if path.is_file():
            if path.name == "sw.js":
                # Service worker must live at root to control scope '/'
                shutil.copy2(path, output_dir / path.name)
            else:
                shutil.copy2(path, static_dir / path.name)
        elif path.is_dir():
            shutil.copytree(path, static_dir / path.name, dirs_exist_ok=True)

    # PHP files → output root
    php_dir = BASE_DIR / "php"
    for path in php_dir.iterdir():
        if path.is_file() and path.suffix == ".php":
            shutil.copy2(path, output_dir / path.name)

    # PHP includes
    includes_dir = output_dir / "includes"
    includes_dir.mkdir(parents=True, exist_ok=True)
    for path in (php_dir / "includes").iterdir():
        if path.is_file():
            shutil.copy2(path, includes_dir / path.name)

    # CSS for PHP inlining (header.php reads __DIR__/../style.css)
    shutil.copy2(_CSS_PATH, output_dir / "style.css")

    # Config / static files from the in-repo public_html
    repo_public = BASE_DIR / "public_html"
    for name in (".htaccess", "404.html", "robots.txt"):
        src = repo_public / name
        if src.is_file():
            shutil.copy2(src, output_dir / name)


def _build_to_dir(output_dir: Path, args: argparse.Namespace) -> None:
    """Generate all site content into output_dir."""
    session = get_session()
    try:
        columns = _get_builder_columns()
        states = all_state_names(session)
        css = _load_css()
        css_hash = hashlib.sha256(css.encode()).hexdigest()[:10]
        css_link = _css_link_tag(css_hash)

        # All visible reaches — used for GeoJSON/map (includes map_only)
        all_reaches = reaches_query(session, visible_only=True, with_gauge=True)
        # Index/CSV/text reaches exclude map_only
        index_reaches = [r for r in all_reaches if not r.map_only]

        print(f"Building site: {len(index_reaches)} reaches")

        # Pre-load data for all reaches at gauge level
        gauge_ids = [r.gauge_id for r in all_reaches if r.gauge_id]
        calculated_gauge_ids = get_calculated_gauge_ids(session, gauge_ids)
        all_latest = get_all_latest_gauges(session, gauge_ids)

        # Deploy source files (static assets, PHP, config)
        _deploy_source_files(output_dir)

        # Generated static assets
        static_dir = output_dir / "static"
        shutil.copy2(_JS_PATH, static_dir / "levels.js")
        shutil.copy2(_FILTERS_JS_PATH, static_dir / "filters.js")
        # Content-hashed stylesheet — cacheable forever (URL changes on content
        # change). Sidecar lets PHP header.php pick up the same hashed URL so
        # static and dynamic pages share one cache entry.
        (static_dir / f"style-{css_hash}.css").write_text(css)
        (static_dir / "style.css.hash").write_text(css_hash)

        # Split the reach dataset into a stable-geometry file (long-cached,
        # content-hashed URL) and a hourly-changing per-reach status file.
        static_json = _build_reaches_static(all_reaches)
        state_json = _build_reaches_state(all_reaches, calculated_gauge_ids, all_latest)
        geom_hash = hashlib.sha256(static_json.encode()).hexdigest()[:10]
        _atomic_write(static_dir / "reaches-geom.json", static_json)
        _atomic_write(static_dir / "reaches-state.json", state_json)
        logger.info(
            "reaches-geom.json: %d bytes; reaches-state.json: %d bytes",
            len(static_json),
            len(state_json),
        )
        # Drop the retired combined file if an older build left one behind.
        with suppress(FileNotFoundError):
            (static_dir / "reaches.geojson").unlink()

        geom_url = f"/static/reaches-geom.json?v={geom_hash}"
        state_url = "/static/reaches-state.json"
        map_html = _build_map_page(css_link, states, geom_url, state_url)
        _atomic_write(output_dir / "map.html", map_html)

        # index.html = all reaches levels table (excludes map_only). Data
        # spans every state, so this is the "all page" that gets the state
        # filter group in the filter bar. state="" keeps the nav bar with
        # no state highlighted, the title as plain "River Levels", and the
        # companion CSV/text at levels.csv / levels.text rather than
        # mis-labeling them as Oregon-specific.
        _build_and_write(
            session,
            index_reaches,
            columns,
            "",
            states,
            css_link,
            output_dir,
            filename="index.html",
            preloaded=(calculated_gauge_ids, all_latest),
            is_all_page=True,
        )

        # gauges.html — supplemental all-gauges listing. Re-fetch the cache
        # over every gauge id it knows about so we also surface gauges with
        # no reach linkage (orphans / future reach work).
        gauges_latest = get_all_latest_gauges(
            session,
            list(session.scalars(select(LatestGaugeObservation.gauge_id).distinct())),
        )
        _write_gauges_page(session, gauges_latest, states, css_link, output_dir)

        # Links pages for all nav states (including Oregon)
        for state in _NAV_STATES:
            if state in states:
                links_page = _build_placeholder_page(css_link, states, state)
                _atomic_write(output_dir / f"{state}.html", links_page)

        _emit_sitemap(output_dir, states, index_reaches, session)
    finally:
        session.close()


def _emit_sitemap(
    output_dir: Path,
    states: list[str],
    reaches: list[Reach],
    session: Session,
) -> None:
    """Emit a sitemap.xml covering every public landing URL.

    Includes the index, each state's letter page, the gauges/map listings,
    the static prose pages, every visible reach's description page, and
    every gauge.php detail page. Dynamic search and account endpoints are
    deliberately omitted (already Disallow'd in robots.txt).
    """
    site = SITE_URL.rstrip("/")
    urls: list[tuple[str, str, str]] = []  # (loc, changefreq, priority)

    urls.append((f"{site}/", "hourly", "1.0"))
    urls.append((f"{site}/gauges.html", "hourly", "0.8"))
    urls.append((f"{site}/map.html", "daily", "0.8"))
    urls.append((f"{site}/custom_gauges.php", "daily", "0.6"))
    for state in states:
        urls.append((f"{site}/{state}.html", "hourly", "0.9"))
    urls.append((f"{site}/about.php", "monthly", "0.4"))
    urls.append((f"{site}/disclaimer.php", "monthly", "0.4"))
    urls.append((f"{site}/privacy.php", "monthly", "0.4"))
    urls.append((f"{site}/contact.php", "monthly", "0.4"))

    for r in reaches:
        urls.append((f"{site}/description.php?id={r.id}", "hourly", "0.7"))

    for gid in session.scalars(select(Gauge.id).order_by(Gauge.id)).all():
        urls.append((f"{site}/gauge.php?id={gid}", "hourly", "0.6"))

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for loc, freq, pri in urls:
        lines.append(
            f"  <url><loc>{loc}</loc>"
            f"<changefreq>{freq}</changefreq>"
            f"<priority>{pri}</priority></url>"
        )
    lines.append("</urlset>")
    _atomic_write(output_dir / "sitemap.xml", "\n".join(lines) + "\n")


def _set_acls(directory: Path) -> None:
    """Set POSIX ACLs so www-data can read the deployed directory.

    No-op on systems without setfacl (e.g. macOS dev workstations) — the
    ACLs are a Linux-prod concern and macOS just isn't going to have a
    www-data user anyway.
    """
    import subprocess

    if shutil.which("setfacl") is None:
        logger.debug("setfacl not on PATH — skipping ACL apply on %s", directory)
        return

    subprocess.run(
        ["setfacl", "-R", "-m", "u:www-data:rX", str(directory)],
        check=True,
    )
    subprocess.run(
        ["setfacl", "-R", "-d", "-m", "u:www-data:rX", str(directory)],
        check=True,
    )


def _deploy_staging_to_live(staging: Path, live: Path) -> set[Path]:
    """Copy every regular file in *staging* into *live* via per-file rename.

    For each file under ``staging``:
      1. Ensure the matching parent dir in ``live`` exists.
      2. If a same-content file already exists at the destination, leave
         it alone — preserving the live file's mtime keeps PHP cache-bust
         URLs (filemtime-derived ``?v=…``) stable across rebuilds when
         only metadata churned upstream.
      3. Otherwise ``shutil.copy2`` to ``<live>/<rel>.new`` — preserves
         mode + xattrs (Linux ACLs live in xattrs, so ``u:www-data:rX``
         carries over from a staging tree run through ``_set_acls``).
      4. ``os.replace`` the temp file over the final name — atomic
         rename(2) on the same filesystem.

    Returns the set of relative paths installed, for the orphan sweep.

    ``staging`` and ``live`` must be on the same filesystem. Symlinks and
    empty directories in ``staging`` are skipped — only regular files are
    propagated.
    """
    staging = staging.resolve()
    live = live.resolve()
    kept: set[Path] = set()
    for src in staging.rglob("*"):
        if not src.is_file() or src.is_symlink():
            continue
        rel = src.relative_to(staging)
        dst = live / rel
        kept.add(rel)
        if dst.exists() and not dst.is_symlink() and filecmp.cmp(src, dst, shallow=False):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".new")
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    return kept


def _sweep_orphans(live: Path, kept: set[Path]) -> list[Path]:
    """Delete files in *live* whose relpaths aren't in *kept*.

    Called after ``_deploy_staging_to_live`` so files left over from a
    previous build (but not produced by this one) get removed. Empty
    directories are left alone — harmless, and avoids a race with any
    concurrent reader.

    Returns the list of relative paths removed, for the build log.
    """
    live = live.resolve()
    removed: list[Path] = []
    for p in live.rglob("*"):
        if not p.is_file() or p.is_symlink():
            continue
        rel = p.relative_to(live)
        if rel not in kept:
            p.unlink()
            removed.append(rel)
    return removed


def build(args: argparse.Namespace) -> None:
    """Generate static HTML/CSV/text files into output_dir.

    Builds to a sibling ``.staging`` directory, applies ACLs, then per-file
    rename-replaces each output into output_dir and sweeps orphans. The
    per-file rename keeps every URL atomic — a request always sees either
    the old or new file, never a half-written one — without ever swapping
    a symlink under in-flight PHP requests.
    """
    output_dir = Path(
        getattr(args, "output_dir", None)
        or os.environ.get("OUTPUT_DIR")
        or str(BASE_DIR / "public_html")
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    staging = output_dir.parent / f"{output_dir.name}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        _build_to_dir(staging, args)
        # Set ACLs on staging so shutil.copy2 carries them via xattrs into
        # each <live>/<file>.new temp, which then rename-replaces the final.
        # Without this, copy2's empty-xattr copy would clobber the inherited
        # default ACL on every deploy and www-data would lose read access.
        _set_acls(staging)
        kept = _deploy_staging_to_live(staging, output_dir)
        removed = _sweep_orphans(output_dir, kept)
        print(
            f"Build complete → {output_dir} ({len(kept)} installed, {len(removed)} orphans removed)"
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _build_and_write(
    session: Session,
    reaches: list[Reach],
    columns: list[dict[str, Any]],
    state: str,
    states: list[str],
    css_link: str,
    output_dir: Path,
    *,
    is_all_page: bool = False,
    preloaded: tuple[set[int], dict[tuple[int, DataType], LatestGaugeObservation]] | None = None,
    filename: str | None = None,
) -> None:
    """Build and write CSV, text, and HTML for a state (or all)."""
    suffix = f"_{state}" if state else ""
    label = state or "all"
    if filename is None:
        filename = f"{state}.html" if state else "all.html"
    title = f"{state} River Levels" if state else "River Levels"

    logger.info("Building %s: %d reaches", label, len(reaches))

    # Pre-load ALL data at gauge level (or reuse preloaded)
    gauge_ids = [r.gauge_id for r in reaches if r.gauge_id]
    if preloaded:
        calculated_gauge_ids, all_latest = preloaded
    else:
        calculated_gauge_ids = get_calculated_gauge_ids(session, gauge_ids)
        all_latest = get_all_latest_gauges(session, gauge_ids)
    sparkline_obs = _select_sparkline_series(session, gauge_ids)

    # CSV
    csv_content = _build_csv(reaches, columns, state, calculated_gauge_ids, all_latest)
    _atomic_write(output_dir / f"levels{suffix}.csv", csv_content)

    # Text
    text_content = _build_text(reaches, columns, state, calculated_gauge_ids, all_latest)
    _atomic_write(output_dir / f"levels{suffix}.text", text_content)

    # HTML — sparklines loaded lazily via JS
    table_html, letters = _build_html_table(
        reaches, columns, calculated_gauge_ids, all_latest, is_all_page=is_all_page
    )
    huc6_names: dict[str, str] = {
        row.code: row.name for row in session.scalars(select(HucName).where(HucName.level == 6))
    }
    filter_data = _collect_filter_data(reaches, calculated_gauge_ids, all_latest, huc6_names)
    filter_bar_html = _build_filter_bar(filter_data, is_all_page=is_all_page)
    page_html = _build_page(
        table_html,
        css_link,
        states,
        state,
        title,
        letters=letters,
        filter_bar_html=filter_bar_html,
        path=f"/{filename}",
    )
    _atomic_write(output_dir / filename, page_html)

    # Sparklines JSON — keyed by gauge_id, loaded by levels.js after paint
    sparklines: dict[str, str] = {}
    for reach in reaches:
        if reach.gauge and reach.gauge.id not in sparklines:
            svg = _build_sparkline(reach, sparkline_obs)
            if svg:
                sparklines[str(reach.gauge.id)] = svg
    static_dir = output_dir / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(static_dir / "sparklines.json", json.dumps(sparklines))
