"""Builder command — generates static HTML/CSV/text files to disk.

Writes complete, self-contained HTML pages with inlined CSS to an output
directory (default: public_html/).  Each page has responsive mobile-first
styling, state navigation links, and inline SVG sparklines.
"""

import argparse
import csv
import html as html_mod
import io
import json
import logging
import os
import shutil
import tempfile
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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
from kayak.db.models import DataType, LatestGaugeObservation, Observation, Reach
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
SPARKLINE_COLOR = "#2060A0"

# Data freshness
DATA_STALE_THRESHOLD = timedelta(hours=48)
DATA_EXPIRY_THRESHOLD = timedelta(days=7)
SPARKLINE_OBSERVATION_WINDOW = timedelta(hours=48)

# GeoJSON geometry simplification
GEOJSON_SIMPLIFY_EPSILON = 0.001
GEOJSON_COORD_PRECISION = 5

# Branding
BRAND_COLOR = "#2060A0"


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

_LEVELS_JS_VERSION = int(_JS_PATH.stat().st_mtime)
_LEVELS_JS = f'<script src="/static/levels.js?v={_LEVELS_JS_VERSION}" defer></script>'


def _load_css() -> str:
    try:
        return _CSS_PATH.read_text()
    except FileNotFoundError:
        logger.warning("style.css not found at %s", _CSS_PATH)
        return ""


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
        "state": ", ".join(s.name for s in reach.states) if reach.states else "",
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

    records = sparkline_obs.get(gauge.id, [])
    if len(records) < 3:
        return ""

    # Build (epoch, value) pairs sorted by time
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
    """Return a hashable key representing a reach's flow level thresholds."""
    if not reach.levels:
        return ()
    return tuple(
        sorted(
            (str(sl.level), sl.low, str(sl.low_data_type), sl.high, str(sl.high_data_type))
            for sl in reach.levels
        )
    )


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


def _compute_gauge_groups(visible: list[tuple[Reach, dict]]) -> list[int]:
    """Compute rowspan groups for consecutive reaches sharing the same gauge.

    Returns a list of the same length as *visible*. For the first row in each
    group, the value is the group size (rowspan). For subsequent rows, the
    value is 0 (meaning gauge-specific columns are spanned by the first row).
    """
    group_span: list[int] = [0] * len(visible)
    i = 0
    while i < len(visible):
        reach_i = visible[i][0]
        if not reach_i.gauge_id:
            group_span[i] = 1
            i += 1
            continue
        key = (reach_i.gauge_id, _levels_key(reach_i))
        j = i + 1
        while j < len(visible):
            reach_j = visible[j][0]
            if not reach_j.gauge_id:
                break
            if (reach_j.gauge_id, _levels_key(reach_j)) != key:
                break
            j += 1
        group_span[i] = j - i
        i = j
    return group_span


def _format_cell_value(col: dict[str, Any], row: dict, reach_id: int, gauge_id: int | None) -> str:
    """Format a single table cell value based on its column type."""
    val = row.get(col["field"], "")

    if col["type"] == "name":
        est = '<span class="est"> (est)</span>' if row.get("is_estimated") else ""
        return f'<a href="/description.php?id={reach_id}">{html_mod.escape(str(val))}</a>{est}'
    elif col["type"] == "flow" and isinstance(val, int | float):
        lvl = html_mod.escape(str(row["flow_level"])) if row.get("flow_level") else ""
        lvl_cls = f' class="level-{lvl}"' if lvl else ""
        gid_attr = f' data-gid="{gauge_id}"' if gauge_id else ""
        return f'<span{lvl_cls}>{val:,.0f}</span><span class="spark"{gid_attr}></span>'
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


def _build_html_table(
    reaches: list[Reach],
    columns: list[dict[str, Any]],
    calculated_gauge_ids: set[int],
    all_latest: dict[tuple[int, DataType], LatestGaugeObservation],
    *,
    is_all_page: bool = False,
) -> tuple[str, list[str]]:
    """Build the <table> body for a set of reaches using pre-loaded data.

    Three phases:
      1. Filter to visible rows (have current data, not expired)
      2. Compute gauge groups (consecutive reaches sharing a gauge get rowspan)
      3. Render HTML rows with formatted cell values

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
    group_span = _compute_gauge_groups(visible)

    # Render rows
    prev_letter = ""
    letters: list[str] = []
    for idx, (reach, row) in enumerate(visible):
        reach_id = reach.id
        gauge_id = reach.gauge.id if reach.gauge else None
        span = group_span[idx]
        is_first = span > 0

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
            f'<tr{letter_id} class="clickable-row{stale}" data-href="/description.php?id={reach_id}">'
        )

        for col in columns:
            if "h" not in col["use"] or col["type"] == "noop":
                continue
            if col["field"] == "state" and not is_all_page:
                continue

            is_gauge_col = col["field"] in _GAUGE_FIELDS
            if is_gauge_col and not is_first:
                continue  # spanned by earlier row

            val = _format_cell_value(col, row, reach_id, gauge_id)
            label = col["name_text"]
            td_cls = _TD_CLASS.get(col["type"], "")
            if col["field"] in _SECONDARY_FIELDS:
                td_cls = (td_cls + " secondary").strip()

            cls_attr = f' class="{td_cls}"' if td_cls else ""
            rowspan = f' rowspan="{span}"' if is_gauge_col and span > 1 else ""
            lines.append(
                f'  <td{cls_attr}{rowspan} data-label="{html_mod.escape(label)}">{val}</td>'
            )
        lines.append("</tr>")

    lines.append("</tbody></table>")
    return "\n".join(lines), letters


def _build_geojson(
    reaches: list[Reach],
    calculated_gauge_ids: set[int],
    all_latest: dict[tuple[int, DataType], LatestGaugeObservation],
    epsilon: float = GEOJSON_SIMPLIFY_EPSILON,
) -> str:
    """Build a GeoJSON FeatureCollection of all mappable reaches."""
    features: list[dict] = []
    for reach in reaches:
        row = _get_row_data(reach, calculated_gauge_ids, all_latest)
        if row.get("expired"):
            continue
        status = row.get("status", "unknown")
        name = reach.display_name or reach.name or ""
        props = {"id": reach.id, "name": name, "status": status}

        geometry = None
        if reach.geom:
            points = parse_geom(reach.geom)
            if len(points) >= 2:
                simplified = simplify(points, epsilon)
                p = GEOJSON_COORD_PRECISION
                coords = [[round(x, p), round(y, p)] for x, y in simplified]
                geometry = {"type": "LineString", "coordinates": coords}
            elif len(points) == 1:
                p = GEOJSON_COORD_PRECISION
                geometry = {
                    "type": "Point",
                    "coordinates": [round(points[0][0], p), round(points[0][1], p)],  # type: ignore[arg-type]
                }
        if (
            geometry is None
            and reach.latitude_start
            and reach.longitude_start
            and reach.latitude_end
            and reach.longitude_end
        ):
            p = GEOJSON_COORD_PRECISION
            coords = [
                [round(float(reach.longitude_start), p), round(float(reach.latitude_start), p)],
                [round(float(reach.longitude_end), p), round(float(reach.latitude_end), p)],
            ]
            geometry = {"type": "LineString", "coordinates": coords}
        if geometry is None and reach.latitude and reach.longitude:
            p = GEOJSON_COORD_PRECISION
            geometry = {
                "type": "Point",
                "coordinates": [round(float(reach.longitude), p), round(float(reach.latitude), p)],  # type: ignore[arg-type]
            }
        if geometry is None:
            continue

        features.append({"type": "Feature", "properties": props, "geometry": geometry})

    collection = {"type": "FeatureCollection", "features": features}
    return json.dumps(collection, separators=(",", ":"))


def _build_nav(states: list[str], active_state: str = "") -> str:
    """Build abbreviation-based nav bar. OR links to index.html, others to {State}.html."""
    links: list[str] = []
    links.append('<a href="/map.html">Map</a>')
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


def _build_letter_nav(letters: list[str]) -> str:
    """Build an A-Z letter navigation bar linking to #letter-X anchors."""
    if not letters:
        return ""
    links = " ".join(f'<a href="#letter-{ch}">{ch}</a>' for ch in letters)
    return f'<nav class="letter-nav" aria-label="Jump to river by letter">{links}</nav>'


def _build_page(
    table_html: str,
    css: str,
    states: list[str],
    current_state: str,
    title: str,
    letters: list[str] | None = None,
) -> str:
    """Wrap the table HTML in a complete HTML document with inlined CSS."""
    nav_html = _build_nav(states, active_state=current_state)
    letter_nav_html = _build_letter_nav(letters) if letters else ""
    now_utc = datetime.now(UTC)
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    now_display = now_utc.strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="{BRAND_COLOR}">
<link rel="icon" href="/static/favicon.ico">
<link rel="apple-touch-icon" href="/static/icon-180.png">
<style>
{css}
</style>
</head>
<body>
<a href="#main" class="skip-link">Skip to main content</a>
<header>
  <h1><a href="/index.html">River Levels</a></h1>
  <nav aria-label="State navigation">
    {nav_html}
  </nav>
  {letter_nav_html}
</header>
<main id="main">
{table_html}
<div style="font-size:.75rem;color:#555;margin-top:1rem;line-height:1.6">
<p><b>Status:</b>
<span class="level-low">Low</span> &ndash;
<span class="level-okay">Okay</span> &ndash;
<span class="level-high">High</span>
(thresholds set per reach based on flow or gage height)</p>
</div>
<p style="font-size:.7rem;color:#888;margin-top:.5rem">Updated <time datetime="{now_iso}">{now_display}</time></p>
</main>
<footer>
Data sourced from USGS, NOAA, USACE, USBR, and other government agencies. <a href="/privacy.php">Privacy Policy</a>
</footer>
{_LEVELS_JS}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Placeholder page — non-primary states
# ---------------------------------------------------------------------------


def _build_placeholder_page(css: str, states: list[str], state: str) -> str:
    """Build a links page for a non-primary state."""
    nav_html = _build_nav(states, active_state=state)
    links = _STATE_LINKS.get(state, [])
    link_items = "\n".join(f'<li><a href="{url}">{label}</a></li>' for label, url in links)
    links_html = f"<ul>\n{link_items}\n</ul>" if links else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{state} River Levels</title>
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="{BRAND_COLOR}">
<link rel="icon" href="/static/favicon.ico">
<link rel="apple-touch-icon" href="/static/icon-180.png">
<style>
{css}
</style>
</head>
<body>
<header>
  <h1><a href="/index.html">River Levels</a></h1>
  <nav aria-label="State navigation">
    {nav_html}
  </nav>
</header>
<main>
<h2>{state}</h2>
{links_html}
</main>
<footer>
Data sourced from USGS, NOAA, USACE, USBR, and other government agencies. <a href="/privacy.php">Privacy Policy</a>
</footer>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Map page
# ---------------------------------------------------------------------------


def _build_map_page(css: str, states: list[str]) -> str:
    """Build map.html with an interactive Leaflet map of Oregon reaches."""
    nav_html = _build_nav(states)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>River Map</title>
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="{BRAND_COLOR}">
<link rel="icon" href="/static/favicon.ico">
<link rel="apple-touch-icon" href="/static/icon-180.png">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha384-sHL9NAb7lN7rfvG5lfHpm643Xkcjzp4jFvuavGOndn6pjVqS6ny56CAt3nsEVT4H" crossorigin="anonymous"/>
<style>
{css}
#map {{height:calc(100vh - 5rem);width:100%;}}
.legend {{background:var(--c-surface);padding:8px 12px;border-radius:4px;box-shadow:0 1px 4px rgba(0,0,0,.3);line-height:1.6;font-size:.85rem;}}
.legend i {{width:14px;height:14px;display:inline-block;margin-right:6px;border-radius:2px;vertical-align:middle;}}
main {{padding:0;max-width:none;}}
</style>
</head>
<body>
<header>
  <h1><a href="/index.html">River Levels</a></h1>
  <nav aria-label="State navigation">
    {nav_html}
  </nav>
</header>
<main>
<div id="map"></div>
</main>
<footer>
Data sourced from USGS, NOAA, USACE, USBR, and other government agencies. <a href="/privacy.php">Privacy Policy</a>
</footer>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha384-cxOPjt7s7Iz04uaHJceBmS+qpjv2JkIHNVcuOrM+YHwZOmJGBXI00mdUXEq65HTH" crossorigin="anonymous"></script>
<script src="/static/map.js"></script>
</body>
</html>"""


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
    # Static assets (icons, JS, manifest, service worker)
    static_dir = output_dir / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    src_static = BASE_DIR / "static"
    for path in src_static.iterdir():
        if path.is_file():
            shutil.copy2(path, static_dir / path.name)

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
    for name in (".htaccess", "404.html", "robots.txt", "no_show_review.js", "no_show_review.html"):
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

        # All visible gauged reaches — used for index.html, GeoJSON, CSV, text
        all_reaches = reaches_query(session, visible_only=True, with_gauge=True)

        print(f"Building site: {len(all_reaches)} reaches")

        # Pre-load data for all reaches at gauge level
        gauge_ids = [r.gauge_id for r in all_reaches if r.gauge_id]
        calculated_gauge_ids = get_calculated_gauge_ids(session, gauge_ids)
        all_latest = get_all_latest_gauges(session, gauge_ids)

        # Deploy source files (static assets, PHP, config)
        _deploy_source_files(output_dir)

        # Generated static assets
        static_dir = output_dir / "static"
        shutil.copy2(_JS_PATH, static_dir / "levels.js")

        # GeoJSON → static/reaches.geojson
        geojson = _build_geojson(all_reaches, calculated_gauge_ids, all_latest)
        _atomic_write(static_dir / "reaches.geojson", geojson)
        logger.info("GeoJSON: %d bytes", len(geojson))

        # Map page → map.html
        map_html = _build_map_page(css, states)
        _atomic_write(output_dir / "map.html", map_html)

        # index.html = all reaches levels table
        _build_and_write(
            session,
            all_reaches,
            columns,
            PRIMARY_STATE,
            states,
            css,
            output_dir,
            filename="index.html",
            preloaded=(calculated_gauge_ids, all_latest),
        )

        # Links pages for all nav states (including Oregon)
        for state in _NAV_STATES:
            if state in states:
                links_page = _build_placeholder_page(css, states, state)
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
        # --- Atomic deploy mode ---
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
        output_dir.mkdir(parents=True, exist_ok=True)
        _build_to_dir(output_dir, args)
        print(f"Build complete → {output_dir}")


def _build_and_write(
    session: Session,
    reaches: list[Reach],
    columns: list[dict[str, Any]],
    state: str,
    states: list[str],
    css: str,
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
    since_48h = datetime.now(UTC) - SPARKLINE_OBSERVATION_WINDOW
    sparkline_obs = get_bulk_gauge_observations(session, gauge_ids, DataType.flow, since_48h)
    inflow_obs = get_bulk_gauge_observations(session, gauge_ids, DataType.inflow, since_48h)
    for gid, obs in inflow_obs.items():
        sparkline_obs.setdefault(gid, obs)

    # CSV
    csv_content = _build_csv(reaches, columns, state, calculated_gauge_ids, all_latest)
    _atomic_write(output_dir / f"levels{suffix}.csv", csv_content)

    # Text
    text_content = _build_text(reaches, columns, state, calculated_gauge_ids, all_latest)
    _atomic_write(output_dir / f"levels{suffix}.text", text_content)

    # HTML — complete self-contained page (sparklines loaded lazily via JS)
    table_html, letters = _build_html_table(
        reaches, columns, calculated_gauge_ids, all_latest, is_all_page=is_all_page
    )
    page_html = _build_page(table_html, css, states, state, title, letters=letters)
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
