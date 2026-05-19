"""Gauges page — supplemental all-gauges listing."""

import html as html_mod
import json
import logging
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from kayak.config import BASE_DIR
from kayak.db.gauges import get_calculated_gauge_ids
from kayak.db.models import DataType, Gauge, HucName, LatestGaugeObservation, Reach
from kayak.web.build._shared import (
    _ABBR_TO_STATE,
    DATA_EXPIRY_THRESHOLD,
    DATA_STALE_THRESHOLD,
    _atomic_write,
)
from kayak.web.build.levels import _build_filter_bar, _get_row_data
from kayak.web.build.shell import _build_page
from kayak.web.build.sparklines import _select_sparkline_series, _sparkline_svg_from_records

logger = logging.getLogger(__name__)


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


_GAUGE_DTYPE_ORDER: tuple[tuple[str, DataType], ...] = (
    ("flow", DataType.flow),
    ("gage", DataType.gauge),
    ("temperature", DataType.temperature),
    ("inflow", DataType.inflow),
)


def _resolve_gauge_display(
    g: Gauge,
    reaches: list[Reach],
    metadata: dict[str, dict[str, str]],
) -> tuple[str, str, str, str]:
    """Return ``(river, location, display_name, sort_name)`` for one gauge.

    Prefers the pre-normalized columns populated by
    ``scripts/seed_gauge_display.py``. The resolver-based fallback only
    fires for brand-new rows inserted after the last seeder run.
    """
    if g.river is not None:
        river = g.river
        location = g.location or ""
        return river, location, g.display_name or river, g.sort_name or river.lower()
    reach_river = next((r.river for r in reaches if r.river), "")
    river, location = _resolve_river_location(g, metadata, reach_river)
    display_name = f"{river} at {location}" if river and location else river or location
    # Best-effort key so unseeded rows still land in a sensible slot.
    elev = float(g.elevation) if g.elevation is not None else None
    elev_key = f"{round(10000 - elev):06d}" if elev is not None else "999999"
    return river, location, display_name, f"{river.lower()}|9|{elev_key}|999999"


def _merge_gauge_observations(
    row: dict[str, Any],
    gauge_id: int,
    all_latest: dict[tuple[int, DataType], LatestGaugeObservation],
) -> None:
    """Populate flow/gage/temperature/time on *row* from pre-loaded latest data.

    ``inflow`` collapses into the ``flow`` column if no direct flow has
    been seen — otherwise it is dropped, matching the reach-table priority.
    """
    for dtype_name, dtype in _GAUGE_DTYPE_ORDER:
        latest = all_latest.get((gauge_id, dtype))
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


def _gauge_observation_age(row: dict[str, Any]) -> object:
    """Return the staleness sentinel for *row*: ``"expired"``, ``"stale"``, or ``None``.

    The string sentinels mirror the reach-side semantics so the caller can
    drop expired gauges and tag stale ones without re-parsing the timestamp.
    """
    obs_time = row.get("time")
    if not isinstance(obs_time, datetime):
        return None
    if obs_time.tzinfo is None:
        obs_time = obs_time.replace(tzinfo=UTC)
    age = datetime.now(UTC) - obs_time
    if age > DATA_EXPIRY_THRESHOLD:
        return "expired"
    if age > DATA_STALE_THRESHOLD:
        return "stale"
    return None


def _apply_gauge_metadata(row: dict[str, Any], g: Gauge) -> None:
    """Fill state/HUC/drainage/elevation columns from the gauge row.

    Filter pills come straight from the gauge row — gauges.html no longer
    walks linked reaches for state/HUC. ``data-state`` on the row is the
    full state name (matches reach-side convention); the table cell still
    shows the postal abbreviation.
    """
    state_abbrev = g.state or ""
    gauge_huc = g.huc or ""
    row["state"] = _ABBR_TO_STATE.get(state_abbrev, "")
    row["state_abbrev"] = state_abbrev
    row["huc6"] = gauge_huc[:6] if len(gauge_huc) >= 6 else ""
    row["huc8"] = gauge_huc[:8] if len(gauge_huc) >= 8 else ""
    row["has_huc"] = bool(row["huc8"])
    row["drainage_area"] = float(g.drainage_area) if g.drainage_area is not None else None
    row["elevation"] = float(g.elevation) if g.elevation is not None else None


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
        river, location, display_name, sort_name = _resolve_gauge_display(g, reaches, metadata)
        row: dict[str, Any] = {
            "gauge_id": g.id,
            "river": river,
            "location": location,
            "display_name": display_name,
            "sort_name": sort_name,
            "is_estimated": g.id in calc_ids,
        }
        _merge_gauge_observations(row, g.id, all_latest)
        if not any(k in row for k in ("flow", "gage", "temperature")):
            continue
        age_tag = _gauge_observation_age(row)
        if age_tag == "expired":
            continue
        if age_tag == "stale":
            row["stale"] = True
        _apply_gauge_metadata(row, g)
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

        # aria-hidden on the placeholder so SR skips empty spans; the
        # decorative SVG inside (when JS resolves it) is also aria-hidden.
        lines.append(
            f'  <td class="td-spark secondary" data-label="2-day Trend">'
            f'<span class="spark" data-gid="{gid}" aria-hidden="true"></span></td>'
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
    *,
    is_all_page: bool = True,
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
    return _build_filter_bar(filter_data, is_all_page=is_all_page)


def _write_gauges_page(
    session: Session,
    all_latest: dict[tuple[int, DataType], LatestGaugeObservation],
    states: list[str],
    css_link: str,
    output_dir: Path,
    *,
    state: str | None = None,
) -> bool:
    """Render gauges.html (or a state-scoped variant) and merge sparklines.

    ``state`` is a state abbreviation ("MT", "OR", ...) — when set, filter
    rows to that state and emit ``gauges.<state-lower>.html``. Returns
    True when a page was written, False when the state filter produced
    no rows (e.g. the migration landed but the first fetch hasn't run yet).
    """
    metadata = _load_station_metadata()
    gauge_ids_with_data = list({gid for gid, _ in all_latest})
    calc_ids = get_calculated_gauge_ids(session, gauge_ids_with_data)
    rows = _collect_gauge_rows(session, all_latest, metadata, calc_ids)

    if state is not None:
        # _apply_gauge_metadata stores the FULL state name on the row
        # (`row["state"] = _ABBR_TO_STATE.get(...)`), not the abbreviation.
        # Match against the full name so the public API takes the postal
        # abbreviation ("MT") and the internal filter still lines up.
        state_full = _ABBR_TO_STATE.get(state, state)
        rows = [r for r in rows if r.get("state") == state_full]
        if not rows:
            logger.info("No gauges to render for state=%s; skipping page", state)
            return False
        filename = f"gauges.{state_full.lower().replace(' ', '_')}.html"
        title = f"River Gauges — {state_full}"
        current_state = state_full
    else:
        filename = "gauges.html"
        title = "River Gauges"
        current_state = ""

    logger.info("Building %s: %d gauges", filename, len(rows))
    print(f"Building {filename}: {len(rows)} gauges")

    table_html, letters = _build_gauges_table(rows)
    huc6_names: dict[str, str] = {
        r.code: r.name for r in session.scalars(select(HucName).where(HucName.level == 6))
    }
    huc8_names: dict[str, str] = {
        r.code: r.name for r in session.scalars(select(HucName).where(HucName.level == 8))
    }
    filter_bar_html = _build_gauges_filter_bar(
        rows, huc6_names, huc8_names, is_all_page=(state is None)
    )
    page_html = _build_page(
        table_html,
        css_link,
        states,
        current_state=current_state,
        title=title,
        letters=letters,
        filter_bar_html=filter_bar_html,
        active_page="gauges",
        picker_kind="gauge",
        path=f"/{filename}",
    )
    _atomic_write(output_dir / filename, page_html)

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
    return True
