"""Builder command — generates static HTML/CSV/text files to disk.

Writes complete, self-contained HTML pages with inlined CSS to an output
directory (default: public_html/).  Each page has responsive mobile-first
styling, state navigation links, and inline SVG sparklines.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kayak.config import BASE_DIR
from kayak.config_data import load_builder_columns
from kayak.db.data_db import get_latest, get_observations
from kayak.db.engine import get_session
from kayak.db.info_db import (
    all_state_names,
    classify_level,
    get_primary_source_id,
    is_source_calculated,
    sections_query,
)
from kayak.db.models import DataType, Section
from kayak.utils.lttb import downsample, running_median

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

def _get_row_data(session, section: Section) -> dict:
    """Build a data dict for one river section."""
    row: dict = {
        "section_id": section.id,
        "display_name": section.display_name or "",
        "gauge_location": (section.gauge.location if section.gauge else "") or "",
        "drainage": section.basin or "",
        "class": "",
        "state": ", ".join(s.name for s in section.states) if section.states else "",
        "db_name": section.name,
    }

    if section.classes:
        row["class"] = ", ".join(c.name for c in section.classes)

    gauge = section.gauge
    if gauge:
        source_id = get_primary_source_id(session, gauge.id)
        if source_id:
            # Check if source is calculated (estimated)
            if is_source_calculated(session, source_id):
                row["is_estimated"] = True

            for dtype_name, dtype in [
                ("flow", DataType.flow),
                ("gage", DataType.gauge),
                ("temperature", DataType.temperature),
            ]:
                latest = get_latest(session, source_id, dtype)
                if latest and latest.value is not None:
                    row[dtype_name] = latest.value
                    row["time"] = latest.observed_at
                    if latest.delta_per_hour is not None:
                        if abs(latest.delta_per_hour) < 0.5:
                            row["status"] = "stable"
                        elif latest.delta_per_hour > 0:
                            row["status"] = "rising"
                        else:
                            row["status"] = "falling"

                    # Classify flow/gage level
                    if dtype_name in ("flow", "gage"):
                        level = classify_level(section, dtype, latest.value)
                        if level:
                            row[f"{dtype_name}_level"] = str(level)

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

def _build_sparkline(session, section: Section, width: int = 80, height: int = 20) -> str:
    """Generate a tiny inline SVG sparkline for the last 48h of flow data."""
    gauge = section.gauge
    if not gauge:
        return ""
    source_id = get_primary_source_id(session, gauge.id)
    if not source_id:
        return ""

    since = datetime.now(UTC) - timedelta(hours=48)
    records = get_observations(session, source_id, DataType.flow, since=since)
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

def _build_csv(session, sections, columns, state_name: str) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    headers = [c["name_text"] for c in columns if "c" in c["use"] and c["type"] != "noop"]
    writer.writerow(headers)

    for section in sections:
        row = _get_row_data(session, section)
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


def _build_text(session, sections, columns, state_name: str) -> str:
    lines = []
    header = ""
    for col in columns:
        if "t" not in col["use"] or col["type"] == "noop":
            continue
        header += col["name_text"].ljust(col["length"])
    lines.append(header)
    lines.append("-" * len(header))

    for section in sections:
        row = _get_row_data(session, section)
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
_SECONDARY_FIELDS = {"drainage", "class"}


def _build_html_table(session, sections, columns) -> str:
    """Build the <table> body for a set of sections."""
    rows: list[str] = []
    rows.append('<table class="levels">')
    rows.append("<thead><tr>")
    for col in columns:
        if "h" not in col["use"] or col["type"] == "noop":
            continue
        cls = ' class="secondary"' if col["field"] in _SECONDARY_FIELDS else ""
        rows.append(f"  <th{cls}>{col['name_html']}</th>")
    rows.append("</tr></thead>")
    rows.append("<tbody>")

    for section in sections:
        row = _get_row_data(session, section)
        if row.get("expired"):
            continue
        section_id = section.id
        sparkline = _build_sparkline(session, section)
        tr_cls = ' class="stale"' if row.get("stale") else ""
        rows.append(f"<tr{tr_cls}>")

        for col in columns:
            if "h" not in col["use"] or col["type"] == "noop":
                continue

            val = row.get(col["field"], "")
            label = col["name_text"]
            td_cls = _TD_CLASS.get(col["type"], "")
            if col["field"] in _SECONDARY_FIELDS:
                td_cls = (td_cls + " secondary").strip()

            if col["type"] == "name":
                est = '<span class="est"> (est)</span>' if row.get("is_estimated") else ""
                val = f'<a href="/description.php?id={section_id}">{val}</a>{est}'
            elif col["type"] == "flow" and isinstance(val, (int, float)):
                lvl_cls = f' class="level-{row["flow_level"]}"' if row.get("flow_level") else ""
                val = (
                    f'<a{lvl_cls} href="/plot.php?type=flow&id={section_id}">{val:.0f}</a>'
                    f"{sparkline}"
                )
            elif col["type"] == "gage" and isinstance(val, (int, float)):
                lvl_cls = f' class="level-{row["gage_level"]}"' if row.get("gage_level") else ""
                val = f'<a{lvl_cls} href="/plot.php?type=gage&id={section_id}">{val:.1f}</a>'
            elif col["type"] == "temp" and isinstance(val, (int, float)):
                val = f'<a href="/plot.php?type=temp&id={section_id}">{val:.1f}</a>'
            elif col["type"] == "date" and isinstance(val, datetime):
                iso = val.strftime("%Y-%m-%dT%H:%M:%SZ")
                display = val.strftime("%m/%d %H:%M")
                val = f'<time datetime="{iso}">{display}</time>'
            elif col["type"] == "status":
                status = row.get("status", "")
                val = f'<span class="{status}">{status}</span>' if status else ""
            else:
                val = str(val) if val else ""

            cls_attr = f' class="{td_cls}"' if td_cls else ""
            rows.append(f'  <td{cls_attr} data-label="{label}">{val}</td>')
        rows.append("</tr>")

    rows.append("</tbody></table>")
    return "\n".join(rows)


def _build_page(table_html: str, css: str, states: list[str],
                current_state: str, title: str) -> str:
    """Wrap the table HTML in a complete HTML document with inlined CSS."""
    nav_links: list[str] = []
    all_cls = ' class="active"' if not current_state else ""
    nav_links.append(f'<a href="/all.html"{all_cls}>All</a>')
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
    for s in states:
        nav_links.append(f'<a href="/{s}.html">{s}</a>')
    nav_html = "\n    ".join(nav_links)

    state_cards: list[str] = []
    state_cards.append('<a href="/all.html" class="state-card">All States</a>')
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
<p style="margin-top:1rem"><a href="https://wkcc.org">Washington Kayak Club</a></p>
</main>
<footer>
Data sourced from USGS, NOAA, USACE, USBR, and other government agencies.
</footer>
{_LOCAL_TIME_JS}
<script>if('serviceWorker' in navigator)navigator.serviceWorker.register('/static/sw.js')</script>
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
        all_sections = sections_query(session, visible_only=True, with_gauge=True)
        states = all_state_names(session)
        css = _load_css()

        print(f"Building pages for {len(all_sections)} sections across {len(states)} states")

        # Landing page → index.html (lightweight state list)
        landing_html = _build_landing_page(css, states)
        (output_dir / "index.html").write_text(landing_html)

        # All-states page → all.html
        _build_and_write(session, all_sections, columns, "", states, css, output_dir)

        # Per-state pages
        for state in states:
            state_sections = sections_query(session, state_name=state, visible_only=True)
            if state_sections:
                _build_and_write(session, state_sections, columns, state, states, css, output_dir)

        print(f"Build complete → {output_dir}")
    finally:
        session.close()


def _build_and_write(session, sections, columns, state: str,
                     states: list[str], css: str, output_dir: Path):
    """Build and write CSV, text, and HTML for a state (or all)."""
    suffix = f"_{state}" if state else ""
    label = state or "all"
    filename = f"{state}.html" if state else "all.html"
    title = f"{state} River Levels" if state else "River Levels"

    logger.info("Building %s: %d sections", label, len(sections))

    # CSV
    csv_content = _build_csv(session, sections, columns, state)
    (output_dir / f"levels{suffix}.csv").write_text(csv_content)

    # Text
    text_content = _build_text(session, sections, columns, state)
    (output_dir / f"levels{suffix}.text").write_text(text_content)

    # HTML — complete self-contained page
    table_html = _build_html_table(session, sections, columns)
    page_html = _build_page(table_html, css, states, state, title)
    (output_dir / filename).write_text(page_html)
