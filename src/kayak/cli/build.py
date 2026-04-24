"""Builder command — generates static HTML/CSV/text files to disk.

Writes complete, self-contained HTML pages with inlined CSS to an output
directory (default: public_html/).  Each page has responsive mobile-first
styling, state navigation links, and inline SVG sparklines.
"""

import argparse
import csv
import hashlib
import html as html_mod
import io
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import tempfile
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from kayak.config import BASE_DIR
from kayak.config_data import load_builder_columns
from kayak.db.data_db import get_all_latest_gauges, get_bulk_gauge_observations
from kayak.db.engine import get_session
from kayak.db.info_db import (
    all_state_names,
    classify_level,
    get_calculated_gauge_ids,
    reaches_query,
)
from kayak.db.models import DataType, Gauge, HucName, LatestGaugeObservation, Observation, Reach
from kayak.utils.class_tiers import parse_class_tiers
from kayak.utils.lttb import downsample, running_median
from kayak.utils.simplify import parse_geom, simplify

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — extracted from inline magic numbers
# ---------------------------------------------------------------------------

# Sparkline rendering
SPARKLINE_MEDIAN_WINDOW_SECS = 3 * 3600  # 3-hour running median window
SPARKLINE_DOWNSAMPLE_POINTS = 60  # Target points after LTTB downsampling
SPARKLINE_DEFAULT_WIDTH = 80
SPARKLINE_DEFAULT_HEIGHT = 20
SPARKLINE_STROKE_WIDTH = "1.5"
SPARKLINE_COLOR = "#1b5591"

# Data freshness
DATA_STALE_THRESHOLD = timedelta(hours=48)
DATA_EXPIRY_THRESHOLD = timedelta(days=7)
SPARKLINE_OBSERVATION_WINDOW = timedelta(hours=48)

# Sparkline series-selection freshness: a series is considered "current" if
# its most recent observation is within this window. Used to decide whether
# flow/inflow is current enough to plot, or to fall back to gauge height.
SPARKLINE_CURRENT_WINDOW = timedelta(hours=6)

# GeoJSON geometry simplification. Coordinate precision is matched to the
# simplify epsilon - quantizing below the simplification grid would be wasted
# bytes. At 44N, 1e-4 deg ~= 8-11 m, below NHD's horizontal accuracy.
GEOJSON_SIMPLIFY_EPSILON = 0.001
GEOJSON_COORD_PRECISION = 4
assert math.ceil(-math.log10(GEOJSON_SIMPLIFY_EPSILON)) + 1 <= GEOJSON_COORD_PRECISION

# Branding
BRAND_COLOR = "#1b5591"


def _atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* atomically via temp file + rename."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, content.encode())
        os.close(fd)
        fd = -1
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        with suppress(OSError):
            os.unlink(tmp)
        raise


PRIMARY_STATE = "Oregon"

_STATE_ABBREVS = {
    "Arizona": "AZ",
    "California": "CA",
    "Colorado": "CO",
    "Idaho": "ID",
    "Kansas": "KS",
    "Montana": "MT",
    "Nevada": "NV",
    "New Mexico": "NM",
    "Oregon": "OR",
    "Utah": "UT",
    "Washington": "WA",
    "Wyoming": "WY",
}

# States shown in the nav bar (Oregon + adjacent states)
_NAV_STATES = {"Oregon", "Washington", "Idaho", "Nevada", "California"}

# Links for adjacent state pages
_STATE_LINKS: dict[str, list[tuple[str, str]]] = {
    "Oregon": [
        (
            "American Whitewater — Oregon",
            "https://www.americanwhitewater.org/content/River/view/river-index/state/USA-ORE",
        ),
        (
            "Dreamflows — Oregon Coastal",
            "https://www.dreamflows.com/flows.php?zone=panw&page=prod&form=norm&mark=All#Oregon_Coastal_Rivers",
        ),
        (
            "Dreamflows — Oregon Central",
            "https://www.dreamflows.com/flows.php?zone=panw&page=prod&form=norm&mark=All#Oregon_Central_Rivers",
        ),
        (
            "Dreamflows — Oregon Eastern",
            "https://www.dreamflows.com/flows.php?zone=panw&page=prod&form=norm&mark=All#Oregon_Eastern_Rivers",
        ),
        ("Oregon Kayaking", "https://oregonkayaking.net"),
        ("USGS Oregon Water Data", "https://waterdata.usgs.gov/state/oregon/"),
        ("NW River Forecast Center", "https://www.nwrfc.noaa.gov/rfc/"),
        ("USBR Hydromet", "https://www.usbr.gov/pn/hydromet/datamenu.html"),
        ("Willamette Kayak and Canoe Club", "https://wkcc.org"),
        ("Oregon Whitewater Association", "https://oregonwhitewater.org"),
        ("Oregon Weather — Windy", "https://www.windy.com/?44.0,-120.5,7"),
    ],
    "Washington": [
        (
            "American Whitewater — Washington",
            "https://www.americanwhitewater.org/content/River/view/river-index/state/USA-WSH",
        ),
        (
            "Dreamflows — Washington",
            "https://www.dreamflows.com/flows.php?zone=panw&page=prod&form=norm&mark=All#Washington_Rivers",
        ),
        ("USGS Washington Water Data", "https://waterdata.usgs.gov/state/washington/"),
        ("NW River Forecast Center", "https://www.nwrfc.noaa.gov/rfc/"),
        ("USBR Hydromet", "https://www.usbr.gov/pn/hydromet/datamenu.html"),
        ("Professor Paddle", "https://www.professorpaddle.com"),
        ("Washington Weather — Windy", "https://www.windy.com/?47.5,-120.5,7"),
        ("Washington Kayak Club", "http://wakayakclub.clubexpress.com"),
    ],
    "Idaho": [
        (
            "American Whitewater — Idaho",
            "https://www.americanwhitewater.org/content/River/view/river-index/state/USA-IDA",
        ),
        (
            "Dreamflows — Idaho",
            "https://www.dreamflows.com/flows.php?zone=panw&page=prod&form=norm&mark=All#Idaho_Rivers",
        ),
        ("USGS Idaho Water Data", "https://waterdata.usgs.gov/state/idaho/"),
        ("NW River Forecast Center", "https://www.nwrfc.noaa.gov/rfc/"),
        ("USBR Hydromet", "https://www.usbr.gov/pn/hydromet/datamenu.html"),
        ("Idaho Rivers United", "https://www.idahorivers.org"),
        ("Idaho Whitewater Association", "https://idahowhitewater.org"),
        ("Idaho Dept. of Water Resources", "https://idwr.idaho.gov"),
        ("Idaho Weather — Windy", "https://www.windy.com/?44.4,-114.7,7"),
    ],
    "Nevada": [
        ("USGS Nevada Water Data", "https://waterdata.usgs.gov/state/nevada/"),
        ("Colorado Basin River Forecast Center", "https://www.cbrfc.noaa.gov"),
        (
            "American Whitewater — Nevada",
            "https://www.americanwhitewater.org/content/River/view/river-index/state/USA-NEV",
        ),
        ("USBR Hydromet", "https://www.usbr.gov/pn/hydromet/datamenu.html"),
        ("Nevada Weather — Windy", "https://www.windy.com/?39.5,-116.9,7"),
    ],
    "California": [
        ("Dreamflows", "https://www.dreamflows.com"),
        (
            "American Whitewater — California",
            "https://www.americanwhitewater.org/content/River/view/river-index/state/USA-CAL",
        ),
        ("USGS California Water Data", "https://waterdata.usgs.gov/state/california/"),
        ("California Nevada River Forecast Center", "https://www.cnrfc.noaa.gov"),
        ("California Creeks", "https://cacreeks.com"),
        ("Gold Country Paddlers", "https://goldcountrypaddlers.org"),
        ("California Weather — Windy", "https://www.windy.com/?37.2,-119.5,6"),
    ],
}

# CSS is read once from the source tree and inlined into every page.
_STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"
_CSS_PATH = _STATIC_DIR / "style.css"
_JS_PATH = _STATIC_DIR / "levels.js"
_FILTERS_JS_PATH = _STATIC_DIR / "filters.js"

_LEVELS_JS_VERSION = int(_JS_PATH.stat().st_mtime)
_FILTERS_JS_VERSION = int(_FILTERS_JS_PATH.stat().st_mtime)
_LEVELS_JS = f'<script src="/static/levels.js?v={_LEVELS_JS_VERSION}" defer></script>'


def _load_css() -> str:
    try:
        return _CSS_PATH.read_text()
    except FileNotFoundError:
        logger.warning("style.css not found at %s", _CSS_PATH)
        return ""


def _css_link_tag(css_hash: str) -> str:
    """Return the <link> tag that replaces per-page inline CSS."""
    return f'<link rel="stylesheet" href="/static/style-{css_hash}.css">'


def _get_builder_columns() -> list[dict]:
    cols = load_builder_columns()
    return sorted(cols, key=lambda c: c["sort_key"])


# ---------------------------------------------------------------------------
# Row data
# ---------------------------------------------------------------------------


def _get_row_data(
    reach: Reach,
    calculated_gauge_ids: set[int],
    all_latest: dict[tuple[int, DataType], LatestGaugeObservation],
) -> dict:
    """Build a data dict for one river reach using pre-loaded gauge-level data."""
    row: dict = {
        "reach_id": reach.id,
        "display_name": reach.display_name or "",
        "gauge_location": reach.description or (reach.gauge.location if reach.gauge else "") or "",
        "drainage": reach.basin or "",
        "class": "",
        # Render the cell as the 2-letter abbreviation (rightmost column on
        # index.html). Filter still uses full state names via data-state.
        "state": ", ".join(_STATE_ABBREVS.get(s.name, s.name) for s in reach.states)
        if reach.states
        else "",
        "db_name": reach.name,
    }

    if reach.classes:
        row["class"] = ", ".join(c.name for c in reach.classes)

    gauge = reach.gauge
    if gauge:
        if gauge.id in calculated_gauge_ids:
            row["is_estimated"] = True

        for dtype_name, dtype in [
            ("flow", DataType.flow),
            ("gage", DataType.gauge),
            ("temperature", DataType.temperature),
            ("inflow", DataType.inflow),
        ]:
            latest = all_latest.get((gauge.id, dtype))
            if latest and latest.value is not None:
                # Display inflow in the flow column if no direct flow
                display_name = dtype_name
                if dtype_name == "inflow" and "flow" not in row:
                    display_name = "flow"
                elif dtype_name == "inflow":
                    continue
                row[display_name] = latest.value
                if "time" not in row or latest.observed_at > row["time"]:
                    row["time"] = latest.observed_at
                # Classify flow/gage level (inflow uses flow thresholds)
                classify_dtype = DataType.flow if dtype == DataType.inflow else dtype
                if display_name in ("flow", "gage"):
                    level = classify_level(reach, classify_dtype, latest.value)
                    if level:
                        row[f"{display_name}_level"] = str(level)
                        if "status" not in row:
                            row["status"] = str(level)

        # Stale / expired detection
        if "time" in row:
            obs_time = row["time"]
            if obs_time.tzinfo is None:
                obs_time = obs_time.replace(tzinfo=UTC)
            age = datetime.now(UTC) - obs_time
            if age > DATA_EXPIRY_THRESHOLD:
                row["expired"] = True
            elif age > DATA_STALE_THRESHOLD:
                row["stale"] = True
    return row


# ---------------------------------------------------------------------------
# Sparkline SVG
# ---------------------------------------------------------------------------


def _select_sparkline_series(
    session: Session, gauge_ids: list[int]
) -> dict[int, list[Observation]]:
    """Choose which data-type series drives each gauge's sparkline.

    Per-gauge preference: flow → inflow → gauge, taking whichever has a
    latest observation within ``SPARKLINE_CURRENT_WINDOW``. If flow or
    inflow has only stale points, we fall through to gauge-height rather
    than draw a multi-day-old flow line. Stored values are naive-UTC in
    SQLite, so we compare against ``datetime.now(UTC)`` after stamping UTC.
    """
    since_48h = datetime.now(UTC) - SPARKLINE_OBSERVATION_WINDOW
    current_cutoff = datetime.now(UTC) - SPARKLINE_CURRENT_WINDOW
    flow_obs = get_bulk_gauge_observations(session, gauge_ids, DataType.flow, since_48h)
    inflow_obs = get_bulk_gauge_observations(session, gauge_ids, DataType.inflow, since_48h)
    gauge_obs = get_bulk_gauge_observations(session, gauge_ids, DataType.gauge, since_48h)

    def _is_current(obs: list[Observation] | None) -> bool:
        if not obs:
            return False
        latest = max(o.observed_at for o in obs)
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=UTC)
        return latest >= current_cutoff

    selected: dict[int, list[Observation]] = {}
    for gid in gauge_ids:
        for series in (flow_obs.get(gid), inflow_obs.get(gid), gauge_obs.get(gid)):
            if _is_current(series):
                selected[gid] = series  # type: ignore[assignment]
                break
    return selected


def _sparkline_svg_from_records(
    records: list[Observation],
    width: int = 80,
    height: int = 20,
) -> str:
    """Render the sparkline SVG from raw observations. Empty if insufficient data."""
    if len(records) < 3:
        return ""

    pairs = sorted(
        [(r.observed_at.timestamp(), r.value) for r in records if r.value is not None],
        key=lambda p: p[0],
    )
    if len(pairs) < 3:
        return ""

    pairs = running_median(pairs, window_seconds=SPARKLINE_MEDIAN_WINDOW_SECS)
    pairs = downsample(pairs, SPARKLINE_DOWNSAMPLE_POINTS)

    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    x_min, x_max = xs[0], xs[-1]
    y_min, y_max = min(ys), max(ys)

    x_range = x_max - x_min or 1
    y_range = y_max - y_min or 1

    points = " ".join(
        f"{int((x - x_min) / x_range * width)},{int(height - (y - y_min) / y_range * height)}"
        for x, y in pairs
    )

    return (
        f'<svg class="spark" width="{width}" height="{height}" viewBox="0 0 {width} {height}" aria-hidden="true">'
        f'<polyline fill="none" stroke="{SPARKLINE_COLOR}" stroke-width="{SPARKLINE_STROKE_WIDTH}" points="{points}"/>'
        f"</svg>"
    )


def _build_sparkline(
    reach: Reach,
    sparkline_obs: dict[int, list[Observation]],
    width: int = 80,
    height: int = 20,
) -> str:
    """Generate a tiny inline SVG sparkline from pre-loaded gauge observation data."""
    gauge = reach.gauge
    if not gauge:
        return ""
    return _sparkline_svg_from_records(sparkline_obs.get(gauge.id, []), width, height)


# ---------------------------------------------------------------------------
# CSV / Text builders (unchanged logic)
# ---------------------------------------------------------------------------


def _build_csv(
    reaches: list[Reach],
    columns: list[dict[str, Any]],
    state_name: str,
    calculated_gauge_ids: set[int],
    all_latest: dict[tuple[int, DataType], LatestGaugeObservation],
) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    headers = [c["name_text"] for c in columns if "c" in c["use"] and c["type"] != "noop"]
    writer.writerow(headers)

    for reach in reaches:
        row = _get_row_data(reach, calculated_gauge_ids, all_latest)
        values = []
        for col in columns:
            if "c" not in col["use"] or col["type"] == "noop":
                continue
            val = row.get(col["field"], "")
            if isinstance(val, float):
                val = f"{val:.1f}"
            elif isinstance(val, datetime):
                val = val.strftime("%Y-%m-%d %H:%M")
            values.append(str(val))
        writer.writerow(values)
    return output.getvalue()


def _build_text(
    reaches: list[Reach],
    columns: list[dict[str, Any]],
    state_name: str,
    calculated_gauge_ids: set[int],
    all_latest: dict[tuple[int, DataType], LatestGaugeObservation],
) -> str:
    lines = []
    header = ""
    for col in columns:
        if "t" not in col["use"] or col["type"] == "noop":
            continue
        header += col["name_text"].ljust(col["length"])
    lines.append(header)
    lines.append("-" * len(header))

    for reach in reaches:
        row = _get_row_data(reach, calculated_gauge_ids, all_latest)
        line = ""
        for col in columns:
            if "t" not in col["use"] or col["type"] == "noop":
                continue
            val = row.get(col["field"], "")
            if isinstance(val, float):
                val = f"{val:.1f}"
            elif isinstance(val, datetime):
                val = val.strftime("%m/%d %H:%M")
            line += str(val)[: col["length"]].ljust(col["length"])
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML builder — complete self-contained pages
# ---------------------------------------------------------------------------

# Map column type to CSS class for <td>
_TD_CLASS = {
    "name": "td-name",
    "flow": "td-flow",
    "gage": "td-gage",
    "temp": "td-temp",
    "date": "td-date",
    "status": "td-status",
    "text": "",
}

# Columns that get class="secondary" (hidden on phones)
_SECONDARY_FIELDS = {"drainage", "class", "state"}

# Fields whose cells are gauge-specific and can be consolidated with rowspan
_GAUGE_FIELDS = {"time", "flow", "gage", "temperature", "status"}


def _levels_key(reach: Reach) -> tuple:
    """Return a hashable key representing a reach's flow range.

    Derived from the first reach_class row with populated bounds.
    """
    for rc in reach.classes:
        if rc.low is not None or rc.high is not None:
            return (rc.low, str(rc.low_data_type), rc.high, str(rc.high_data_type))
    return ()


def _filter_visible_rows(
    reaches: list[Reach],
    calculated_gauge_ids: set[int],
    all_latest: dict[tuple[int, DataType], LatestGaugeObservation],
) -> list[tuple[Reach, dict]]:
    """Filter reaches to those with current data and build row dicts.

    Excludes expired reaches (data > 7 days old) and reaches with no
    flow/gage/temperature data. Returns (reach, row_dict) tuples.
    """
    visible: list[tuple[Reach, dict]] = []
    for reach in reaches:
        row = _get_row_data(reach, calculated_gauge_ids, all_latest)
        if row.get("expired"):
            continue
        has_data = any(
            row.get(k) is not None and row.get(k) != "" for k in ("flow", "gage", "temperature")
        )
        if not has_data:
            continue
        visible.append((reach, row))
    return visible


def _format_cell_value(col: dict[str, Any], row: dict, reach_id: int, gauge_id: int | None) -> str:
    """Format a single table cell value based on its column type."""
    val = row.get(col["field"], "")

    if col["type"] == "name":
        est = '<span class="est"> (est)</span>' if row.get("is_estimated") else ""
        return f'<a href="/description.php?id={reach_id}">{html_mod.escape(str(val))}{est}</a>'
    elif col["type"] == "flow":
        # The sparkline slot lives in the flow column regardless of which
        # series drives it — flow, inflow, or (fallback) gauge height. The
        # JS populates `<span class="spark">` elements by data-gid from
        # sparklines.json, so we emit the placeholder whenever a gauge
        # exists even if this reach's flow value itself is empty.
        gid_attr = f' data-gid="{gauge_id}"' if gauge_id else ""
        if isinstance(val, int | float):
            lvl = html_mod.escape(str(row["flow_level"])) if row.get("flow_level") else ""
            lvl_cls = f' class="level-{lvl}"' if lvl else ""
            return f'<span{lvl_cls}>{val:,.0f}</span><span class="spark"{gid_attr}></span>'
        if gauge_id:
            return f'<span class="spark"{gid_attr}></span>'
        return ""
    elif col["type"] == "gage" and isinstance(val, int | float):
        lvl = html_mod.escape(str(row["gage_level"])) if row.get("gage_level") else ""
        lvl_cls = f' class="level-{lvl}"' if lvl else ""
        return f"<span{lvl_cls}>{val:,.1f}</span>"
    elif col["type"] == "temp" and isinstance(val, int | float):
        return f"{val:.1f}"
    elif col["type"] == "date" and isinstance(val, datetime):
        iso = val.strftime("%Y-%m-%dT%H:%M:%SZ")
        display = val.strftime("%m/%d %H:%M")
        return f'<time datetime="{iso}">{display}</time>'
    elif col["type"] == "status":
        status = html_mod.escape(str(row.get("status", "")))
        return f'<span class="level-{status}">{status}</span>' if status else ""
    else:
        return html_mod.escape(str(val)) if val else ""


def _row_filter_attrs(reach: Reach, row: dict) -> str:
    """Build the data-state/basin/huc8/status/tier attr block for one <tr>."""
    state = reach.states[0].name if reach.states else ""
    basin = reach.basin or ""
    huc8 = (reach.huc or "")[:8]
    status = row.get("status") or "unknown"
    tiers: set[str] = set()
    for c in reach.classes:
        tiers.update(parse_class_tiers(c.name))
    ordered = sorted(tiers, key=lambda t: ("I", "II", "III", "IV", "V").index(t))
    tier_attr = ",".join(ordered) if ordered else "?"
    return (
        f' data-state="{html_mod.escape(state)}"'
        f' data-basin="{html_mod.escape(basin)}"'
        f' data-huc8="{html_mod.escape(huc8)}"'
        f' data-status="{html_mod.escape(status)}"'
        f' data-tier="{html_mod.escape(tier_attr)}"'
    )


def _build_html_table(
    reaches: list[Reach],
    columns: list[dict[str, Any]],
    calculated_gauge_ids: set[int],
    all_latest: dict[tuple[int, DataType], LatestGaugeObservation],
    *,
    is_all_page: bool = False,
) -> tuple[str, list[str]]:
    """Build the <table> body for a set of reaches using pre-loaded data.

    Two phases:
      1. Filter to visible rows (have current data, not expired)
      2. Render HTML rows with formatted cell values + filter data-attrs

    Returns (html, letters) where letters is the ordered list of first-letters
    that appear in the visible rows (used for the letter navigation bar).
    """
    lines: list[str] = []
    lines.append('<table class="levels">')
    lines.append("<thead><tr>")
    for col in columns:
        if "h" not in col["use"] or col["type"] == "noop":
            continue
        if col["field"] == "state" and not is_all_page:
            continue
        cls = ' class="secondary"' if col["field"] in _SECONDARY_FIELDS else ""
        lines.append(f'  <th scope="col"{cls}>{col["name_html"]}</th>')
    lines.append("</tr></thead>")
    lines.append("<tbody>")

    visible = _filter_visible_rows(reaches, calculated_gauge_ids, all_latest)

    # Render rows
    prev_letter = ""
    letters: list[str] = []
    for reach, row in visible:
        reach_id = reach.id
        gauge_id = reach.gauge.id if reach.gauge else None

        # Track first-letter groups for the letter navigation bar
        sort_name = reach.sort_name or reach.display_name or ""
        cur_letter = sort_name[0].upper() if sort_name else ""
        letter_id = ""
        if cur_letter and cur_letter != prev_letter:
            letter_id = f' id="letter-{cur_letter}"'
            letters.append(cur_letter)
            prev_letter = cur_letter

        stale = " stale" if row.get("stale") else ""
        lines.append(
            f'<tr{letter_id} class="clickable-row{stale}"'
            f' data-href="/description.php?id={reach_id}"'
            f"{_row_filter_attrs(reach, row)}>"
        )

        for col in columns:
            if "h" not in col["use"] or col["type"] == "noop":
                continue
            if col["field"] == "state" and not is_all_page:
                continue

            val = _format_cell_value(col, row, reach_id, gauge_id)
            label = col["name_text"]
            td_cls = _TD_CLASS.get(col["type"], "")
            if col["field"] in _SECONDARY_FIELDS:
                td_cls = (td_cls + " secondary").strip()

            cls_attr = f' class="{td_cls}"' if td_cls else ""
            lines.append(f'  <td{cls_attr} data-label="{html_mod.escape(label)}">{val}</td>')
        lines.append("</tr>")

    lines.append("</tbody></table>")
    return "\n".join(lines), letters


def _collect_filter_data(
    reaches: list[Reach],
    calculated_gauge_ids: set[int],
    all_latest: dict[tuple[int, DataType], LatestGaugeObservation],
    huc6_names: dict[str, str],
) -> dict[str, Any]:
    """Union of values present across the visible rows, for filter-pill rendering.

    The basin filter is hierarchical: groups HUC8 codes by their HUC6 parent.
    ``huc6_names`` maps 6-digit HUC6 codes to display names (from huc_name).
    """
    visible = _filter_visible_rows(reaches, calculated_gauge_ids, all_latest)
    states: set[str] = set()
    statuses: set[str] = set()
    tiers: set[str] = set()
    # huc6_code -> set of (huc8_code, huc8_name) tuples present in the rows.
    huc6_to_huc8s: dict[str, set[tuple[str, str]]] = {}
    has_no_huc = False
    for reach, row in visible:
        for s in reach.states:
            states.add(s.name)
        statuses.add(row.get("status") or "unknown")
        row_tiers: set[str] = set()
        for c in reach.classes:
            row_tiers.update(parse_class_tiers(c.name))
        if row_tiers:
            tiers.update(row_tiers)
        else:
            tiers.add("?")
        if reach.huc and len(reach.huc) >= 8:
            huc6 = reach.huc[:6]
            huc8 = reach.huc[:8]
            huc6_to_huc8s.setdefault(huc6, set()).add((huc8, reach.basin or huc8))
        else:
            has_no_huc = True
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
    return {
        "state": sorted(s for s in states if s),
        "huc6_groups": huc6_groups,
        "has_no_huc": has_no_huc,
        "status": [s for s in ("low", "okay", "high", "unknown") if s in statuses],
        "tier": [t for t in ("I", "II", "III", "IV", "V", "?") if t in tiers],
    }


def _build_filter_bar(data: dict[str, Any], *, is_all_page: bool) -> str:
    """HTML block rendered above the levels table; hooked up by filters.js."""
    status_swatch = {
        "low": "#e8a735",
        "okay": "#4caf50",
        "high": "#e53935",
        "unknown": "#2196F3",
    }
    status_label = {"low": "Low", "okay": "Okay", "high": "High", "unknown": "Unknown"}

    def pill(group: str, value: str, display: str, swatch: str = "") -> str:
        safe_val = html_mod.escape(value, quote=True)
        safe_disp = html_mod.escape(display)
        sw = f'<span class="swatch" style="background:{swatch}"></span>' if swatch else ""
        return f'<label><input type="checkbox" value="{safe_val}" checked>{sw}{safe_disp}</label>'

    def group_html(
        key: str,
        label: str,
        values: list[str],
        display_fn: Any,
        swatch_fn: Any = lambda v: "",
        split_csv: bool = False,
    ) -> str:
        if not values:
            return ""
        pills = "\n      ".join(pill(key, v, display_fn(v), swatch_fn(v)) for v in values)
        split_attr = ' data-split="csv"' if split_csv else ""
        toggle = (
            '<span class="fg-toggle">'
            '<button type="button" data-all>All</button>'
            '<button type="button" data-none>None</button>'
            "</span>"
        )
        return (
            f'  <details class="filter-group">\n'
            f'    <summary>{label} <span class="fg-count">{len(values)}</span></summary>\n'
            f'    <div class="filter-pills" data-group="{key}"{split_attr}>\n'
            f"      {toggle}\n"
            f"      {pills}\n"
            f"    </div>\n"
            f"  </details>"
        )

    def basin_group_html(huc6_groups: list[dict], has_no_huc: bool) -> str:
        """Render the basin filter as nested HUC6 disclosures with HUC8 child pills.

        The outer filter group has data-group="huc8" — filters.js matches each
        row's data-huc8 against the checked HUC8 pill values. Parent HUC6
        checkboxes are visual-only (data-huc6=...); JS uses them to bulk-toggle
        their children but they are NOT collected into the match logic.
        """
        if not huc6_groups and not has_no_huc:
            return ""
        total = sum(len(g["huc8s"]) for g in huc6_groups) + (1 if has_no_huc else 0)
        toggle = (
            '<span class="fg-toggle">'
            '<button type="button" data-all>All</button>'
            '<button type="button" data-none>None</button>'
            "</span>"
        )
        sub_blocks: list[str] = []
        for g in huc6_groups:
            huc6 = html_mod.escape(g["huc6"], quote=True)
            name = html_mod.escape(g["name"])
            count = len(g["huc8s"])
            child_pills = "\n          ".join(pill("huc8", code, name) for code, name in g["huc8s"])
            sub_blocks.append(
                f'      <details class="filter-subgroup">\n'
                f"        <summary>"
                f'<label class="huc6-parent">'
                f'<input type="checkbox" data-huc6="{huc6}" checked>'
                f"{name}</label>"
                f' <span class="fg-count">{count}</span>'
                f"</summary>\n"
                f'        <div class="filter-pills-sub">\n'
                f"          {child_pills}\n"
                f"        </div>\n"
                f"      </details>"
            )
        if has_no_huc:
            sub_blocks.append(
                '      <div class="filter-pills-sub no-huc-row">\n'
                f"        {pill('huc8', '', '(no HUC)')}\n"
                "      </div>"
            )
        body = "\n".join(sub_blocks)
        return (
            f'  <details class="filter-group" open>\n'
            f'    <summary>Basin <span class="fg-count">{total}</span></summary>\n'
            f'    <div class="filter-pills" data-group="huc8">\n'
            f"      {toggle}\n"
            f"{body}\n"
            f"    </div>\n"
            f"  </details>"
        )

    groups: list[str] = []
    if is_all_page:
        groups.append(group_html("state", "State", data["state"], lambda v: v))
    groups.append(basin_group_html(data["huc6_groups"], data["has_no_huc"]))
    groups.append(
        group_html(
            "status",
            "Status",
            data["status"],
            lambda v: status_label.get(v, v),
            lambda v: status_swatch.get(v, ""),
        )
    )
    # Tiers appear in CSV form on <tr data-tier="III,IV"> so filters.js
    # must split the row's attribute before intersecting with checked pills.
    groups.append(group_html("tier", "Class", data["tier"], lambda v: v, split_csv=True))

    inner = "\n".join(g for g in groups if g)
    # Default-hidden; filters.js injects a "Filter" nav toggle and the user
    # reveals the bar on demand.
    return (
        '<div class="filter-bar" id="filter-bar" hidden>\n'
        f"{inner}\n"
        '  <div class="filter-meta" aria-live="polite">\n'
        '    <span class="fb-count"></span>\n'
        '    <button type="button" class="fb-reset">Reset</button>\n'
        "  </div>\n"
        "</div>"
    )


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


def _build_reaches_state(
    reaches: list[Reach],
    calculated_gauge_ids: set[int],
    all_latest: dict[tuple[int, DataType], LatestGaugeObservation],
) -> str:
    """Flat ``{reach_id: status}`` map — the bit that changes every build."""
    out: dict[str, str] = {}
    for reach in reaches:
        # Only emit reaches whose geometry also makes it into the static
        # file; otherwise the client would carry state it cannot paint.
        if _reach_geometry(reach, GEOJSON_SIMPLIFY_EPSILON) is None:
            continue
        row = _get_row_data(reach, calculated_gauge_ids, all_latest)
        out[str(reach.id)] = row.get("status", "unknown")
    return json.dumps(out, separators=(",", ":"))


def _editor_feature_on() -> bool:
    v = os.environ.get("EDITOR_FEATURE", "").strip().lower()
    return v in ("1", "true", "yes")


def _build_nav(states: list[str], active_state: str = "") -> str:
    """Build abbreviation-based nav bar. OR links to index.html, others to {State}.html."""
    links: list[str] = []
    links.append('<a href="/map.html">Map</a>')
    links.append('<a href="/gauges.html">Gauges</a>')
    for s in states:
        if s not in _NAV_STATES:
            continue
        abbrev = _STATE_ABBREVS.get(s, s)
        cls = ' class="active"' if s == active_state else ""
        href = "/index.html" if s == PRIMARY_STATE else f"/{s}.html"
        links.append(f'<a href="{href}"{cls}>{abbrev}</a>')
    links.append('<a href="/picker.php">Picker</a>')
    links.append('<a href="https://www.windy.com/?44.0,-120.5,7">OR Weather</a>')
    return "\n    ".join(links)


def _build_right_cluster() -> str:
    """Right cluster on the header bar — just WKCC, desktop-only via CSS."""
    return (
        '<nav class="site-nav-right" aria-label="Account and external">'
        '<a href="https://wkcc.org" rel="noopener" target="_blank">WKCC</a>'
        "</nav>"
    )


def _build_footer_html() -> str:
    """Footer shared by all static pages.

    Login and Comment live here (only when EDITOR_FEATURE is on at build
    time) so the header can stay focused on navigation. Contact,
    Disclaimer, and Privacy Policy are always rendered.
    """
    items: list[str] = []
    if _editor_feature_on():
        items.append('<a href="/login.php">Login</a>')
        items.append('<a href="/comment.php">Comment</a>')
    items.append('<a href="/about.php">About</a>')
    items.append('<a href="/contact.php">Contact</a>')
    items.append('<a href="/disclaimer.php">Disclaimer</a>')
    items.append('<a href="/privacy.php">Privacy Policy</a>')
    links = " &middot; ".join(items)
    return (
        "<footer>\n"
        f"<p>{links}</p>\n"
        "<p>Data sourced from USGS, NOAA, USACE, USBR, "
        "and other government agencies.</p>\n"
        "</footer>"
    )


def _build_letter_nav(letters: list[str]) -> str:
    """Build an A-Z letter navigation bar linking to #letter-X anchors."""
    if not letters:
        return ""
    links = " ".join(f'<a href="#letter-{ch}">{ch}</a>' for ch in letters)
    return f'<nav class="letter-nav" aria-label="Jump to river by letter">{links}</nav>'


def _build_page(
    table_html: str,
    css_link: str,
    states: list[str],
    current_state: str,
    title: str,
    letters: list[str] | None = None,
    filter_bar_html: str = "",
) -> str:
    """Wrap the table HTML in a complete HTML document linking to external CSS."""
    nav_html = _build_nav(states, active_state=current_state)
    letter_nav_html = _build_letter_nav(letters) if letters else ""
    now_utc = datetime.now(UTC)
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    now_display = now_utc.strftime("%Y-%m-%d %H:%M UTC")

    desc = (
        f"Real-time river levels, flow, and gage data for {current_state} from USGS, NOAA, USACE, and other agencies."
        if current_state != "All States"
        else "Real-time river levels, flow, and gage data from USGS, NOAA, USACE, and other government agencies."
    )

    filter_tag = (
        f'<script src="/static/filters.js?v={_FILTERS_JS_VERSION}" defer></script>'
        if filter_bar_html
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<meta name="theme-color" content="{BRAND_COLOR}">
<link rel="icon" href="/static/favicon.ico">
{css_link}
</head>
<body>
<a href="#main" class="skip-link">Skip to main content</a>
<header>
  <h1><a href="/index.html">River Levels</a></h1>
  <nav aria-label="State navigation">
    {nav_html}
  </nav>
  {_build_right_cluster()}
  {letter_nav_html}
</header>
<main id="main">
{filter_bar_html}
{table_html}
<div style="font-size:.75rem;color:var(--c-text-muted);margin-top:1rem;line-height:1.6">
<p><b>Status:</b>
<span class="level-low">Low</span> &ndash;
<span class="level-okay">Okay</span> &ndash;
<span class="level-high">High</span>
(thresholds set per reach based on flow or gage height)</p>
</div>
<p style="font-size:.7rem;color:var(--c-text-muted);margin-top:.5rem">Updated <time datetime="{now_iso}">{now_display}</time></p>
</main>
{_build_footer_html()}
{_LEVELS_JS}
{filter_tag}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Placeholder page — non-primary states
# ---------------------------------------------------------------------------


def _build_placeholder_page(css_link: str, states: list[str], state: str) -> str:
    """Build a links page for a non-primary state."""
    nav_html = _build_nav(states, active_state=state)
    links = _STATE_LINKS.get(state, [])
    link_items = "\n".join(
        f'<li><a href="{url}" style="display:inline-flex;align-items:center;min-height:44px">{label}</a></li>'
        for label, url in links
    )
    links_html = f"<ul>\n{link_items}\n</ul>" if links else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{state} River Levels</title>
<meta name="description" content="Real-time river levels, flow, and gage data for {state} from USGS, NOAA, USACE, and other agencies.">
<meta name="theme-color" content="{BRAND_COLOR}">
<link rel="icon" href="/static/favicon.ico">
{css_link}
</head>
<body>
<header>
  <h1><a href="/index.html">River Levels</a></h1>
  <nav aria-label="State navigation">
    {nav_html}
  </nav>
  {_build_right_cluster()}
</header>
<main>
<h2>{state}</h2>
{links_html}
</main>
{_build_footer_html()}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Map page
# ---------------------------------------------------------------------------


def _build_map_page(css_link: str, states: list[str], geom_url: str, state_url: str) -> str:
    """Build map.html with an interactive Leaflet map of all reaches."""
    nav_html = _build_nav(states)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>River Map</title>
<meta name="description" content="Interactive map of river reaches with real-time flow and level data.">
<meta name="theme-color" content="{BRAND_COLOR}">
<link rel="icon" href="/static/favicon.ico">
<link rel="stylesheet" href="/static/leaflet.css">
{css_link}
<style>
#map {{height:calc(100vh - 5rem);width:100%;}}
main {{padding:0;max-width:none;}}
.map-filter{{background:var(--c-surface);padding:6px 10px;border-radius:4px;box-shadow:0 1px 4px rgba(0,0,0,.3);font-size:.85rem;color:var(--c-text);max-width:13rem}}
.map-filter fieldset{{border:0;padding:0;margin:0 0 .35rem}}
.map-filter legend{{font-weight:700;font-size:.75rem;text-transform:uppercase;letter-spacing:.02em;color:var(--c-text-muted);padding:0 0 2px}}
.map-filter label{{display:flex;align-items:center;gap:6px;padding:2px 0;min-height:1.6rem;cursor:pointer}}
.map-filter input[type=checkbox]{{margin:0;flex:0 0 auto}}
.map-filter .swatch{{display:inline-block;width:10px;height:10px;border-radius:2px;border:1px solid rgba(0,0,0,.15)}}
.map-filter .mf-count{{font-size:.75rem;color:var(--c-text-muted);padding-top:2px;border-top:1px solid var(--c-border-light);margin-top:.35rem}}
.map-filter .mf-err{{color:var(--c-low);font-size:.75rem}}
.map-filter-toggle{{display:none;background:var(--c-surface);padding:6px 10px;border:0;border-radius:4px;box-shadow:0 1px 4px rgba(0,0,0,.3);font-size:.85rem;cursor:pointer}}
@media(max-width:640px){{
  .map-filter-toggle{{display:block}}
  .map-filter{{display:none}}
  .map-filter.is-open{{display:block}}
  .map-filter label{{min-height:44px}}
}}
</style>
</head>
<body>
<header>
  <h1><a href="/index.html">River Levels</a></h1>
  <nav aria-label="State navigation">
    {nav_html}
  </nav>
  {_build_right_cluster()}
</header>
<main>
<div id="map" data-geom-url="{html_mod.escape(geom_url, quote=True)}" data-state-url="{html_mod.escape(state_url, quote=True)}"></div>
</main>
{_build_footer_html()}
<script src="/static/leaflet.js" defer></script>
<script src="/static/map.js" defer></script>
</body>
</html>"""


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
    # USGS primary delimiters: AT, NEAR, NR, BLW/BELOW, ABV/ABOVE, and the
    # stray single-letter "A" variant ("KLAMATH R A ORLEANS"). maxsplit=1
    # keeps a secondary "NR" ("AT PETERS PASTURE, NR WARM SPRINGS") inside
    # the location; longer alternatives are listed first so "AT" wins over "A"
    # when both could match the same position.
    parts = re.split(r"\s+(?:ABOVE|BELOW|NEAR|ABV|BLW|AT|NR|A)\s+", s, maxsplit=1)
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


def _collect_gauge_rows(
    session: Session,
    all_latest: dict[tuple[int, DataType], LatestGaugeObservation],
    metadata: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Build one row per gauge with at least one current observation.

    Excludes expired (>7d stale) gauges, mirroring the index page rule. Each
    row carries flow/gage/temperature/time plus state and HUC sets derived
    from any reaches that reference the gauge (used for filter pills).
    """
    gauge_ids_with_data = {gid for gid, _ in all_latest}
    if not gauge_ids_with_data:
        return []

    gauges = list(session.scalars(select(Gauge).where(Gauge.id.in_(gauge_ids_with_data))))
    reach_rows = list(session.scalars(select(Reach).where(Reach.gauge_id.in_(gauge_ids_with_data))))
    gauge_reaches: dict[int, list[Reach]] = {}
    for r in reach_rows:
        if r.gauge_id:
            gauge_reaches.setdefault(r.gauge_id, []).append(r)

    rows: list[dict[str, Any]] = []
    for g in gauges:
        reaches = gauge_reaches.get(g.id, [])
        reach_river = next((r.river for r in reaches if r.river), "")
        river, location = _resolve_river_location(g, metadata, reach_river)

        row: dict[str, Any] = {
            "gauge_id": g.id,
            "river": river,
            "location": location,
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

        states: set[str] = set()
        huc6_to_huc8s: dict[str, set[tuple[str, str]]] = {}
        has_huc = False
        for r in reaches:
            for s in r.states:
                states.add(s.name)
            if r.huc and len(r.huc) >= 8:
                huc6 = r.huc[:6]
                huc8 = r.huc[:8]
                huc6_to_huc8s.setdefault(huc6, set()).add((huc8, r.basin or huc8))
                has_huc = True
        row["states"] = sorted(states)
        row["huc6_to_huc8s"] = huc6_to_huc8s
        row["has_huc"] = has_huc
        row["drainage_area"] = float(g.drainage_area) if g.drainage_area is not None else None
        row["elevation"] = float(g.elevation) if g.elevation is not None else None
        rows.append(row)

    # Within each river: upstream → downstream via elevation DESC (rivers flow
    # downhill, so higher == further up). drainage_area ASC is a secondary
    # tiebreaker for gauges at near-identical elevations. Elevation leads
    # because it's populated more reliably than DA on virtual/side-channel
    # gauges, and never inverts physical order along a flowline.
    def _sort_key(r: dict[str, Any]) -> tuple[Any, ...]:
        da = r.get("drainage_area")
        el = r.get("elevation")
        return (
            r["river"].lower(),
            el is None,
            -el if el is not None else 0.0,
            da is None,
            da if da is not None else 0.0,
            r["location"].lower(),
            r["gauge_id"],
        )

    rows.sort(key=_sort_key)
    return rows


def _build_gauges_table(rows: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """Render the gauges <table>; returns (html, first-letter list for nav)."""
    lines: list[str] = []
    lines.append('<table class="levels">')
    lines.append("<thead><tr>")
    lines.append('  <th scope="col">River</th>')
    lines.append('  <th scope="col">Location</th>')
    lines.append('  <th scope="col">Date</th>')
    lines.append('  <th scope="col">Flow<br>cfs</th>')
    lines.append('  <th scope="col" class="secondary">Spark</th>')
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
        sort_key = river or location
        cur_letter = sort_key[:1].upper() if sort_key else ""
        letter_id = ""
        if cur_letter and cur_letter != prev_letter:
            letter_id = f' id="letter-{cur_letter}"'
            letters.append(cur_letter)
            prev_letter = cur_letter

        state = row["states"][0] if row["states"] else ""
        huc8 = ""
        for huc8s in row["huc6_to_huc8s"].values():
            for code, _name in huc8s:
                huc8 = code
                break
            if huc8:
                break

        stale = " stale" if row.get("stale") else ""
        # Emit filter attrs only when every group's value is populated. A
        # partial set would match the filters.js selector `tr[data-state],...`
        # but then fail match() in any group whose attr is empty, hiding the
        # row permanently. All-or-nothing keeps orphan gauges visible.
        attrs = (
            f' data-state="{html_mod.escape(state)}" data-huc8="{html_mod.escape(huc8)}"'
            if state and huc8
            else ""
        )
        lines.append(
            f'<tr{letter_id} class="clickable-row{stale}" data-href="/gauge.php?id={gid}"{attrs}>'
        )

        lines.append(
            f'  <td class="td-name" data-label="River">'
            f'<a href="/gauge.php?id={gid}">{html_mod.escape(river)}</a></td>'
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
        flow_cell = f"{flow_val:,.0f}" if isinstance(flow_val, int | float) else ""
        lines.append(f'  <td class="td-flow" data-label="Flow">{flow_cell}</td>')

        lines.append(
            f'  <td class="td-spark secondary" data-label="Spark">'
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


def _build_gauges_filter_bar(rows: list[dict[str, Any]], huc6_names: dict[str, str]) -> str:
    """Filter bar for gauges page: State + Basin (status/tier don't apply)."""
    states: set[str] = set()
    huc6_to_huc8s: dict[str, set[tuple[str, str]]] = {}
    has_no_huc = False
    for r in rows:
        states.update(r["states"])
        if r["has_huc"]:
            for huc6, huc8s in r["huc6_to_huc8s"].items():
                huc6_to_huc8s.setdefault(huc6, set()).update(huc8s)
        else:
            has_no_huc = True
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
        "state": sorted(s for s in states if s),
        "huc6_groups": huc6_groups,
        "has_no_huc": has_no_huc,
        "status": [],
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
    rows = _collect_gauge_rows(session, all_latest, metadata)
    logger.info("Building gauges.html: %d gauges", len(rows))
    print(f"Building gauges.html: {len(rows)} gauges")

    table_html, letters = _build_gauges_table(rows)
    huc6_names: dict[str, str] = {
        r.code: r.name for r in session.scalars(select(HucName).where(HucName.level == 6))
    }
    filter_bar_html = _build_gauges_filter_bar(rows, huc6_names)
    page_html = _build_page(
        table_html,
        css_link,
        states,
        current_state="",
        title="River Gauges",
        letters=letters,
        filter_bar_html=filter_bar_html,
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
        # filter group in the filter bar.
        _build_and_write(
            session,
            index_reaches,
            columns,
            PRIMARY_STATE,
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
    finally:
        session.close()


def _set_acls(directory: Path) -> None:
    """Set POSIX ACLs so www-data can read the deployed directory."""
    import subprocess

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
      2. ``shutil.copy2`` to ``<live>/<rel>.new`` — preserves mode + xattrs
         (Linux ACLs live in xattrs, so ``u:www-data:rX`` carries over from
         a staging tree that was run through ``_set_acls``).
      3. ``os.replace`` the temp file over the final name — atomic rename(2)
         on the same filesystem.

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
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".new")
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
        kept.add(rel)
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
    """Generate static HTML/CSV/text files to disk.

    If output_dir is a symlink (production deploy), builds into a fresh
    temporary directory and atomically swaps the symlink.  If it is a
    regular directory, builds in place (development).
    """
    output_dir = Path(
        getattr(args, "output_dir", None)
        or os.environ.get("OUTPUT_DIR")
        or str(BASE_DIR / "public_html")
    )

    if output_dir.is_symlink():
        mode = os.environ.get("KAYAK_DEPLOY_MODE", "")
        if mode == "rename":
            # --- One-shot migration: symlink layout → regular directory ---
            # Linux rename(2) refuses to replace a symlink with a directory
            # (ENOTDIR), so the migration is unlink-then-rename. That opens
            # a ~10-50ms window where the path doesn't exist and requests
            # 404. Acceptable as a single event in the life of the deploy.
            # After this run, output_dir is a regular dir and subsequent
            # builds hit the else branch (in-place rename-over).
            old_target = output_dir.resolve()
            staging = output_dir.parent / f"{output_dir.name}.staging"
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir(parents=True)
            try:
                _build_to_dir(staging, args)
                _set_acls(staging)
                output_dir.unlink()
                try:
                    os.rename(staging, output_dir)
                except BaseException:
                    # Near-impossible on same filesystem; if it happens,
                    # restore the symlink so the site isn't broken.
                    with suppress(Exception):
                        output_dir.symlink_to(old_target)
                    raise
                print(f"Build complete → {output_dir} (migrated symlink → regular dir)")
                # Old dated target is no longer referenced — remove it.
                if old_target.is_dir() and old_target != output_dir.resolve():
                    shutil.rmtree(old_target, ignore_errors=True)
            except BaseException:
                # Migration failed — staging (if it still exists) goes away,
                # symlink is intact (unlinked-then-rename either ran both
                # sides or neither, thanks to the inner restore).
                shutil.rmtree(staging, ignore_errors=True)
                raise
        else:
            # --- Atomic deploy mode (symlink swap) ---
            old_target = output_dir.resolve()
            new_target = output_dir.parent / f"{output_dir.name}_{int(time.time())}"
            new_target.mkdir(parents=True)
            try:
                _build_to_dir(new_target, args)
                _set_acls(new_target)
                # Atomic swap: create temp symlink then rename over the live one
                tmp_link = output_dir.parent / f"{output_dir.name}_tmp"
                tmp_link.symlink_to(new_target)
                tmp_link.rename(output_dir)
                print(f"Build complete → {output_dir} → {new_target}")
                # Remove old target if it differs and still exists
                if old_target != new_target and old_target.is_dir():
                    shutil.rmtree(old_target)
            except BaseException:
                # Clean up the half-built directory on any error
                shutil.rmtree(new_target, ignore_errors=True)
                # Also clean up tmp_link if it was created but rename failed
                with suppress(FileNotFoundError):
                    tmp_link = output_dir.parent / f"{output_dir.name}_tmp"
                    if tmp_link.is_symlink():
                        tmp_link.unlink()
                raise
    else:
        # --- In-place mode (development) ---
        # Opt-in rename-over-target: build into a staging sibling, then
        # rename-copy each file into output_dir and sweep orphans. This is
        # the path being groomed to eventually replace the symlink branch
        # above (iteration 2 of the plan — dev/CI only while it bakes).
        mode = os.environ.get("KAYAK_DEPLOY_MODE", "")
        if mode == "rename":
            staging = output_dir.parent / f"{output_dir.name}.staging"
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir(parents=True)
            try:
                _build_to_dir(staging, args)
                # Set ACLs on staging so shutil.copy2 carries them via xattrs
                # into each <live>/<file>.new temp, which then rename-replaces
                # the final. Without this, copy2's empty-xattr copy would
                # clobber the inherited default ACL on every deploy and
                # www-data would lose read access after a few hours.
                _set_acls(staging)
                output_dir.mkdir(parents=True, exist_ok=True)
                kept = _deploy_staging_to_live(staging, output_dir)
                removed = _sweep_orphans(output_dir, kept)
                print(
                    f"Build complete → {output_dir} "
                    f"(rename mode: {len(kept)} installed, "
                    f"{len(removed)} orphans removed)"
                )
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        else:
            output_dir.mkdir(parents=True, exist_ok=True)
            _build_to_dir(output_dir, args)
            print(f"Build complete → {output_dir}")


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
