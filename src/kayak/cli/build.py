"""Builder command — generates static HTML/CSV/text files to disk.

Writes complete, self-contained HTML pages with inlined CSS to an output
directory (default: public_html/).  Each page has responsive mobile-first
styling, state navigation links, and inline SVG sparklines.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kayak.config import BASE_DIR
from kayak.config_data import load_builder_columns
from kayak.db.data_db import get_all_latest, get_bulk_observations
from kayak.db.engine import get_session
from kayak.db.info_db import (
    all_state_names,
    classify_level,
    get_all_primary_source_ids,
    get_calculated_source_ids,
    reaches_query,
)
from kayak.db.models import DataType, LatestObservation, Observation, Reach
from kayak.utils.lttb import downsample, running_median
from kayak.utils.simplify import parse_geom, simplify

logger = logging.getLogger(__name__)

# CSS is read once from the source tree and inlined into every page.
_CSS_PATH = Path(__file__).resolve().parent.parent / "web" / "static" / "style.css"

# JS snippet to convert <time> elements to the browser's local timezone.
_LOCAL_TIME_JS = """<script>
document.querySelectorAll('time[datetime]').forEach(function(el){
  var d=new Date(el.getAttribute('datetime'));
  if(isNaN(d))return;
  var mm=d.getMonth()+1,dd=d.getDate();
  var hh=d.getHours(),mi=d.getMinutes();
  el.textContent=(mm<10?'0':'')+mm+'/'+(dd<10?'0':'')+dd+' '+(hh<10?'0':'')+hh+':'+(mi<10?'0':'')+mi;
});
</script>"""


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
    primary_source_ids: dict[int, int],
    calculated_ids: set[int],
    all_latest: dict[tuple[int, DataType], LatestObservation],
) -> dict:
    """Build a data dict for one river reach using pre-loaded data."""
    row: dict = {
        "reach_id": reach.id,
        "display_name": reach.display_name or "",
        "gauge_location": (reach.gauge.location if reach.gauge else "") or "",
        "drainage": reach.basin or "",
        "class": "",
        "state": ", ".join(s.name for s in reach.states) if reach.states else "",
        "db_name": reach.name,
    }

    if reach.classes:
        row["class"] = ", ".join(c.name for c in reach.classes)

    gauge = reach.gauge
    if gauge:
        source_id = primary_source_ids.get(gauge.id)
        if source_id:
            if source_id in calculated_ids:
                row["is_estimated"] = True

            for dtype_name, dtype in [
                ("flow", DataType.flow),
                ("gage", DataType.gauge),
                ("temperature", DataType.temperature),
            ]:
                latest = all_latest.get((source_id, dtype))
                if latest and latest.value is not None:
                    row[dtype_name] = latest.value
                    row["time"] = latest.observed_at
                    # Classify flow/gage level
                    if dtype_name in ("flow", "gage"):
                        level = classify_level(reach, dtype, latest.value)
                        if level:
                            row[f"{dtype_name}_level"] = str(level)
                            if "status" not in row:
                                row["status"] = str(level)

            # Stale / expired detection
            if "time" in row:
                obs_time = row["time"]
                if obs_time.tzinfo is None:
                    obs_time = obs_time.replace(tzinfo=UTC)
                age = datetime.now(UTC) - obs_time
                if age > timedelta(days=7):
                    row["expired"] = True
                elif age > timedelta(hours=48):
                    row["stale"] = True
    return row


# ---------------------------------------------------------------------------
# Sparkline SVG
# ---------------------------------------------------------------------------

def _build_sparkline(
    reach: Reach,
    primary_source_ids: dict[int, int],
    sparkline_obs: dict[int, list[Observation]],
    width: int = 80,
    height: int = 20,
) -> str:
    """Generate a tiny inline SVG sparkline from pre-loaded observation data."""
    gauge = reach.gauge
    if not gauge:
        return ""
    source_id = primary_source_ids.get(gauge.id)
    if not source_id:
        return ""

    records = sparkline_obs.get(source_id, [])
    if len(records) < 3:
        return ""

    # Build (epoch, value) pairs sorted by time
    pairs = sorted(
        [(r.observed_at.timestamp(), r.value) for r in records if r.value is not None],
        key=lambda p: p[0],
    )
    if len(pairs) < 3:
        return ""

    pairs = running_median(pairs, window_seconds=3 * 3600)
    pairs = downsample(pairs, 60)

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
        f'<svg class="spark" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<polyline fill="none" stroke="#2060A0" stroke-width="1.5" points="{points}"/>'
        f"</svg>"
    )


# ---------------------------------------------------------------------------
# CSV / Text builders (unchanged logic)
# ---------------------------------------------------------------------------

def _build_csv(reaches, columns, state_name: str,
               primary_source_ids, calculated_ids, all_latest) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    headers = [c["name_text"] for c in columns if "c" in c["use"] and c["type"] != "noop"]
    writer.writerow(headers)

    for reach in reaches:
        row = _get_row_data(reach, primary_source_ids, calculated_ids, all_latest)
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


def _build_text(reaches, columns, state_name: str,
                primary_source_ids, calculated_ids, all_latest) -> str:
    lines = []
    header = ""
    for col in columns:
        if "t" not in col["use"] or col["type"] == "noop":
            continue
        header += col["name_text"].ljust(col["length"])
    lines.append(header)
    lines.append("-" * len(header))

    for reach in reaches:
        row = _get_row_data(reach, primary_source_ids, calculated_ids, all_latest)
        line = ""
        for col in columns:
            if "t" not in col["use"] or col["type"] == "noop":
                continue
            val = row.get(col["field"], "")
            if isinstance(val, float):
                val = f"{val:.1f}"
            elif isinstance(val, datetime):
                val = val.strftime("%m/%d %H:%M")
            line += str(val)[:col["length"]].ljust(col["length"])
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
_GAUGE_FIELDS = {"gauge_location", "time", "flow", "gage", "temperature", "status"}


def _levels_key(reach: Reach) -> tuple:
    """Return a hashable key representing a reach's flow level thresholds."""
    if not reach.levels:
        return ()
    return tuple(sorted(
        (str(sl.level), sl.low, str(sl.low_data_type), sl.high, str(sl.high_data_type))
        for sl in reach.levels
    ))


def _build_html_table(reaches, columns, primary_source_ids, calculated_ids,
                      all_latest, sparkline_obs, *, is_all_page: bool = False) -> str:
    """Build the <table> body for a set of reaches using pre-loaded data."""
    lines: list[str] = []
    lines.append('<table class="levels">')
    lines.append("<thead><tr>")
    for col in columns:
        if "h" not in col["use"] or col["type"] == "noop":
            continue
        if col["field"] == "state" and not is_all_page:
            continue
        cls = ' class="secondary"' if col["field"] in _SECONDARY_FIELDS else ""
        lines.append(f"  <th{cls}>{col['name_html']}</th>")
    lines.append("</tr></thead>")
    lines.append("<tbody>")

    # Phase 1: Build row data and filter
    visible: list[tuple[Reach, dict, str]] = []
    for reach in reaches:
        row = _get_row_data(reach, primary_source_ids, calculated_ids, all_latest)
        if row.get("expired"):
            continue
        has_data = any(row.get(k) is not None and row.get(k) != ""
                       for k in ("flow", "gage", "temperature"))
        if not has_data:
            continue
        sparkline = _build_sparkline(reach, primary_source_ids, sparkline_obs)
        visible.append((reach, row, sparkline))

    # Phase 2: Compute contiguous gauge groups
    # group_span[i] = rowspan for first row in group, 0 for subsequent rows
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
        # subsequent rows in group stay 0
        i = j

    # Phase 3: Render rows
    for idx, (reach, row, sparkline) in enumerate(visible):
        reach_id = reach.id
        span = group_span[idx]
        is_first = span > 0
        tr_cls = ' class="stale"' if row.get("stale") else ""
        lines.append(f"<tr{tr_cls}>")

        for col in columns:
            if "h" not in col["use"] or col["type"] == "noop":
                continue
            if col["field"] == "state" and not is_all_page:
                continue

            is_gauge_col = col["field"] in _GAUGE_FIELDS
            if is_gauge_col and not is_first:
                continue  # spanned by earlier row

            val = row.get(col["field"], "")
            label = col["name_text"]
            td_cls = _TD_CLASS.get(col["type"], "")
            if col["field"] in _SECONDARY_FIELDS:
                td_cls = (td_cls + " secondary").strip()

            if col["type"] == "name":
                est = '<span class="est"> (est)</span>' if row.get("is_estimated") else ""
                val = f'<a href="/description.php?id={reach_id}">{val}</a>{est}'
            elif col["type"] == "flow" and isinstance(val, (int, float)):
                lvl_cls = f' class="level-{row["flow_level"]}"' if row.get("flow_level") else ""
                val = (
                    f'<a{lvl_cls} href="/plot.php?type=flow&id={reach_id}">{val:,.0f}</a>'
                    f"{sparkline}"
                )
            elif col["type"] == "gage" and isinstance(val, (int, float)):
                lvl_cls = f' class="level-{row["gage_level"]}"' if row.get("gage_level") else ""
                val = f'<a{lvl_cls} href="/plot.php?type=gage&id={reach_id}">{val:,.1f}</a>'
            elif col["type"] == "temp" and isinstance(val, (int, float)):
                val = f'<a href="/plot.php?type=temp&id={reach_id}">{val:.1f}</a>'
            elif col["type"] == "date" and isinstance(val, datetime):
                iso = val.strftime("%Y-%m-%dT%H:%M:%SZ")
                display = val.strftime("%m/%d %H:%M")
                val = f'<time datetime="{iso}">{display}</time>'
            elif col["type"] == "status":
                status = row.get("status", "")
                val = f'<span class="level-{status}">{status}</span>' if status else ""
            else:
                val = str(val) if val else ""

            cls_attr = f' class="{td_cls}"' if td_cls else ""
            rowspan = f' rowspan="{span}"' if is_gauge_col and span > 1 else ""
            lines.append(f'  <td{cls_attr}{rowspan} data-label="{label}">{val}</td>')
        lines.append("</tr>")

    lines.append("</tbody></table>")
    return "\n".join(lines)


def _build_reach_directory(reaches) -> str:
    """Build a collapsible alphabetical directory of all reaches."""
    lines: list[str] = []
    lines.append('<details class="reach-dir">')
    lines.append(f'<summary>All Reaches ({len(reaches)})</summary>')
    lines.append('<ul class="reach-list">')
    for reach in reaches:
        name = reach.display_name or reach.name
        lines.append(
            f'<li><a href="/description.php?id={reach.id}">{name}</a></li>'
        )
    lines.append('</ul>')
    lines.append('</details>')
    return "\n".join(lines)


def _build_geojson(
    reaches,
    primary_source_ids: dict[int, int],
    calculated_ids: set[int],
    all_latest: dict,
    epsilon: float = 0.001,
) -> str:
    """Build a GeoJSON FeatureCollection of all mappable reaches."""
    features: list[dict] = []
    for reach in reaches:
        row = _get_row_data(reach, primary_source_ids, calculated_ids, all_latest)
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
                coords = [[round(x, 5), round(y, 5)] for x, y in simplified]
                geometry = {"type": "LineString", "coordinates": coords}
            elif len(points) == 1:
                geometry = {"type": "Point", "coordinates": [round(points[0][0], 5), round(points[0][1], 5)]}
        if geometry is None and reach.latitude_start and reach.longitude_start and reach.latitude_end and reach.longitude_end:
            coords = [
                [round(float(reach.longitude_start), 5), round(float(reach.latitude_start), 5)],
                [round(float(reach.longitude_end), 5), round(float(reach.latitude_end), 5)],
            ]
            geometry = {"type": "LineString", "coordinates": coords}
        if geometry is None and reach.latitude and reach.longitude:
            geometry = {
                "type": "Point",
                "coordinates": [round(float(reach.longitude), 5), round(float(reach.latitude), 5)],
            }
        if geometry is None:
            continue

        features.append({"type": "Feature", "properties": props, "geometry": geometry})

    collection = {"type": "FeatureCollection", "features": features}
    return json.dumps(collection, separators=(",", ":"))


def _build_page(table_html: str, css: str, states: list[str],
                current_state: str, title: str,
                directory_html: str = "") -> str:
    """Wrap the table HTML in a complete HTML document with inlined CSS."""
    nav_links: list[str] = []
    all_cls = ' class="active"' if not current_state else ""
    nav_links.append(f'<a href="/all.html"{all_cls}>All</a>')
    nav_links.append('<a href="/map.html">Map</a>')
    for s in states:
        cls = ' class="active"' if s == current_state else ""
        nav_links.append(f'<a href="/{s}.html"{cls}>{s}</a>')

    nav_html = "\n    ".join(nav_links)
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
<meta name="theme-color" content="#2060A0">
<link rel="icon" href="/static/favicon.ico">
<link rel="apple-touch-icon" href="/static/icon-180.png">
<style>
{css}
</style>
</head>
<body>
<header>
  <h1><a href="/index.html">River Levels</a></h1>
  <nav>
    {nav_html}
  </nav>
</header>
<main>
{table_html}
{directory_html}
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
Data sourced from USGS, NOAA, USACE, USBR, and other government agencies.
</footer>
{_LOCAL_TIME_JS}
<script>if('serviceWorker' in navigator)navigator.serviceWorker.register('/static/sw.js')</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Landing page — lightweight state index
# ---------------------------------------------------------------------------

def _build_landing_page(css: str, states: list[str]) -> str:
    """Build index.html as a simple grid of state links."""
    nav_links: list[str] = []
    nav_links.append('<a href="/all.html">All</a>')
    nav_links.append('<a href="/map.html">Map</a>')
    for s in states:
        nav_links.append(f'<a href="/{s}.html">{s}</a>')
    nav_html = "\n    ".join(nav_links)

    state_cards: list[str] = []
    state_cards.append('<a href="/all.html" class="state-card">All States</a>')
    state_cards.append('<a href="/map.html" class="state-card">Map</a>')
    for s in states:
        state_cards.append(f'<a href="/{s}.html" class="state-card">{s}</a>')
    grid_html = "\n".join(state_cards)

    now_utc = datetime.now(UTC)
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    now_display = now_utc.strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>River Levels</title>
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="#2060A0">
<link rel="icon" href="/static/favicon.ico">
<link rel="apple-touch-icon" href="/static/icon-180.png">
<style>
{css}
</style>
</head>
<body>
<header>
  <h1><a href="/index.html">River Levels</a></h1>
  <nav>
    {nav_html}
  </nav>
</header>
<main>
<div class="state-grid">
{grid_html}
</div>
<p style="font-size:.7rem;color:#888;margin-top:.5rem">Updated <time datetime="{now_iso}">{now_display}</time></p>
<p style="margin-top:1rem"><a href="https://wkcc.org">Willamette Kayak and Canoe Club</a></p>
</main>
<footer>
Data sourced from USGS, NOAA, USACE, USBR, and other government agencies.
</footer>
{_LOCAL_TIME_JS}
<script>if('serviceWorker' in navigator)navigator.serviceWorker.register('/static/sw.js')</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Map page
# ---------------------------------------------------------------------------

def _build_map_page(css: str, states: list[str]) -> str:
    """Build map.html with an interactive Leaflet map of all reaches."""
    nav_links: list[str] = []
    nav_links.append('<a href="/all.html">All</a>')
    nav_links.append('<a href="/map.html" class="active">Map</a>')
    for s in states:
        nav_links.append(f'<a href="/{s}.html">{s}</a>')
    nav_html = "\n    ".join(nav_links)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>River Map</title>
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="#2060A0">
<link rel="icon" href="/static/favicon.ico">
<link rel="apple-touch-icon" href="/static/icon-180.png">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
{css}
#map {{height:calc(100vh - 5rem);width:100%;}}
.legend {{background:#fff;padding:8px 12px;border-radius:4px;box-shadow:0 1px 4px rgba(0,0,0,.3);line-height:1.6;font-size:.85rem;}}
.legend i {{width:14px;height:14px;display:inline-block;margin-right:6px;border-radius:2px;vertical-align:middle;}}
main {{padding:0;max-width:none;}}
</style>
</head>
<body>
<header>
  <h1><a href="/index.html">River Levels</a></h1>
  <nav>
    {nav_html}
  </nav>
</header>
<main>
<div id="map"></div>
</main>
<footer>
Data sourced from USGS, NOAA, USACE, USBR, and other government agencies.
</footer>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
(function(){{
var map=L.map('map').setView([43.5,-115],5);
var topo=L.tileLayer('https://{{s}}.tile.opentopomap.org/{{z}}/{{x}}/{{y}}.png',{{
  maxZoom:17,attribution:'OpenTopoMap'}});
var street=L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{
  maxZoom:19,attribution:'OpenStreetMap'}});
var sat=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}',{{
  maxZoom:18,attribution:'Esri'}});
topo.addTo(map);
L.control.layers({{'Topo':topo,'Street':street,'Satellite':sat}}).addTo(map);

var colors={{okay:'#4caf50',low:'#e8a735',high:'#e53935',unknown:'#2196F3'}};

fetch('/static/reaches.geojson').then(function(r){{return r.json()}}).then(function(data){{
  var geojsonLayer=L.geoJSON(data,{{
    style:function(f){{
      return {{color:colors[f.properties.status]||colors.unknown,weight:3,opacity:0.7}};
    }},
    pointToLayer:function(f,ll){{
      return L.circleMarker(ll,{{radius:6,fillColor:colors[f.properties.status]||colors.unknown,
        color:'#333',weight:1,fillOpacity:0.8}});
    }},
    onEachFeature:function(f,layer){{
      var p=f.properties;
      var badge='<span style="color:'+( colors[p.status]||colors.unknown)+'">&#9679;</span> '+p.status;
      layer.bindPopup('<b><a href="/description.php?id='+p.id+'">'+p.name+'</a></b><br>'+badge);
    }}
  }}).addTo(map);
  if(data.features.length)map.fitBounds(geojsonLayer.getBounds().pad(0.05));
}});

var legend=L.control({{position:'bottomright'}});
legend.onAdd=function(){{
  var d=L.DomUtil.create('div','legend');
  d.innerHTML='<b>Status</b><br>'+
    '<i style="background:#4caf50"></i>Okay<br>'+
    '<i style="background:#e8a735"></i>Low<br>'+
    '<i style="background:#e53935"></i>High<br>'+
    '<i style="background:#2196F3"></i>Unknown';
  return d;
}};
legend.addTo(map);
}})();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def addArgs(subparsers):
    """Register the 'build' subcommand."""
    parser = subparsers.add_parser(
        "build", help="Generate static HTML/CSV/text files to output directory"
    )
    parser.add_argument(
        "--output-dir",
        default=str(BASE_DIR / "public_html"),
        help="Output directory (default: public_html/)",
    )
    parser.set_defaults(func=build)


def build(args):
    """Generate static HTML/CSV/text files to disk."""
    output_dir = Path(getattr(args, "output_dir", None) or str(BASE_DIR / "public_html"))
    output_dir.mkdir(parents=True, exist_ok=True)

    session = get_session()
    try:
        columns = _get_builder_columns()
        all_reaches = reaches_query(session, visible_only=True, with_gauge=True,
                                    sort_by_state=True)
        states = all_state_names(session)
        css = _load_css()

        print(f"Building pages for {len(all_reaches)} reaches across {len(states)} states")

        # Pre-load data for all reaches (used by all-page and GeoJSON)
        all_gauge_ids = [r.gauge_id for r in all_reaches if r.gauge_id]
        all_primary_source_ids = get_all_primary_source_ids(session, all_gauge_ids)
        all_source_ids = list(all_primary_source_ids.values())
        all_calculated_ids = get_calculated_source_ids(session, all_source_ids)
        all_latest = get_all_latest(session, all_source_ids)

        # GeoJSON → static/reaches.geojson
        static_dir = output_dir / "static"
        static_dir.mkdir(parents=True, exist_ok=True)
        geojson = _build_geojson(all_reaches, all_primary_source_ids,
                                 all_calculated_ids, all_latest)
        (static_dir / "reaches.geojson").write_text(geojson)
        logger.info("GeoJSON: %d bytes", len(geojson))

        # Map page → map.html
        map_html = _build_map_page(css, states)
        (output_dir / "map.html").write_text(map_html)

        # Landing page → index.html (lightweight state list)
        landing_html = _build_landing_page(css, states)
        (output_dir / "index.html").write_text(landing_html)

        # All-states page → all.html
        _build_and_write(session, all_reaches, columns, "", states, css, output_dir,
                         is_all_page=True,
                         preloaded=(all_primary_source_ids, all_calculated_ids, all_latest))

        # Per-state pages
        for state in states:
            state_reaches = reaches_query(session, state_name=state, visible_only=True,
                                        with_gauge=True)
            if state_reaches:
                _build_and_write(session, state_reaches, columns, state, states, css, output_dir)

        print(f"Build complete → {output_dir}")
    finally:
        session.close()


def _build_and_write(session, reaches, columns, state: str,
                     states: list[str], css: str, output_dir: Path,
                     *, is_all_page: bool = False,
                     preloaded: tuple | None = None):
    """Build and write CSV, text, and HTML for a state (or all)."""
    suffix = f"_{state}" if state else ""
    label = state or "all"
    filename = f"{state}.html" if state else "all.html"
    title = f"{state} River Levels" if state else "River Levels"

    logger.info("Building %s: %d reaches", label, len(reaches))

    # Pre-load ALL data in ~5 bulk queries (or reuse preloaded)
    if preloaded:
        primary_source_ids, calculated_ids, all_latest = preloaded
        source_ids = list(primary_source_ids.values())
    else:
        gauge_ids = [r.gauge_id for r in reaches if r.gauge_id]
        primary_source_ids = get_all_primary_source_ids(session, gauge_ids)
        source_ids = list(primary_source_ids.values())
        calculated_ids = get_calculated_source_ids(session, source_ids)
        all_latest = get_all_latest(session, source_ids)
    since_48h = datetime.now(UTC) - timedelta(hours=48)
    sparkline_obs = get_bulk_observations(session, source_ids, DataType.flow, since_48h)

    # CSV
    csv_content = _build_csv(reaches, columns, state,
                             primary_source_ids, calculated_ids, all_latest)
    (output_dir / f"levels{suffix}.csv").write_text(csv_content)

    # Text
    text_content = _build_text(reaches, columns, state,
                               primary_source_ids, calculated_ids, all_latest)
    (output_dir / f"levels{suffix}.text").write_text(text_content)

    # HTML — complete self-contained page
    table_html = _build_html_table(reaches, columns, primary_source_ids,
                                   calculated_ids, all_latest, sparkline_obs,
                                   is_all_page=is_all_page)
    directory_html = _build_reach_directory(reaches)
    page_html = _build_page(table_html, css, states, state, title,
                            directory_html=directory_html)
    (output_dir / filename).write_text(page_html)
