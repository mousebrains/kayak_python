"""Builder command (replaces builder.C + Display.C).

Generates per-state HTML, CSV, and text output pages with current
river level data.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime

from kayak.config_data import load_builder_columns
from kayak.db.data_db import get_latest
from kayak.db.engine import get_session
from kayak.db.info_db import all_state_names, get_primary_source_id, sections_query
from kayak.db.models import (
    DataType,
    PageAction,
    Section,
)
from kayak.db.page_db import store_page

logger = logging.getLogger(__name__)


def _get_builder_columns() -> list[dict]:
    """Get builder column definitions sorted by sort_key from YAML."""
    cols = load_builder_columns()
    return sorted(cols, key=lambda c: c["sort_key"])


def _get_row_data(session, section: Section) -> dict:
    """Build a data dict for one river section."""
    row = {
        "section_id": section.id,
        "display_name": section.display_name or "",
        "gauge_location": (section.gauge.location if section.gauge else "") or "",
        "drainage": section.basin or "",
        "class": "",
        "state": ", ".join(s.name for s in section.states) if section.states else "",
        "db_name": section.name,
    }

    # Get class names
    if section.classes:
        row["class"] = ", ".join(c.name for c in section.classes)

    gauge = section.gauge
    if gauge:
        source_id = get_primary_source_id(session, gauge.id)

        if source_id:
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

    return row


def _build_csv(session, sections, columns, state_name: str) -> str:
    """Generate CSV output for a set of sections."""
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
    """Generate fixed-width text output for a set of sections."""
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


def _build_html(session, sections, columns, state_name: str) -> str:
    """Generate HTML table output for a set of sections."""
    rows = []
    rows.append("<table class='levels'>")

    rows.append("<tr>")
    for col in columns:
        if "h" not in col["use"] or col["type"] == "noop":
            continue
        rows.append(f"  <th>{col['name_html']}</th>")
    rows.append("</tr>")

    for section in sections:
        row = _get_row_data(session, section)
        section_id = section.id
        rows.append("<tr>")
        for col in columns:
            if "h" not in col["use"] or col["type"] == "noop":
                continue
            val = row.get(col["field"], "")

            if col["type"] == "name":
                val = f'<a href="?D={section_id}">{val}</a>'
            elif col["type"] == "flow" and isinstance(val, (int, float)):
                val = f'<a href="?f={section_id}">{val:.0f}</a>'
            elif col["type"] == "gage" and isinstance(val, (int, float)):
                val = f'<a href="?g={section_id}">{val:.2f}</a>'
            elif col["type"] == "temp" and isinstance(val, (int, float)):
                val = f'<a href="?t={section_id}">{val:.0f}</a>'
            elif col["type"] == "date" and isinstance(val, datetime):
                val = val.strftime("%m/%d %H:%M")
            elif col["type"] == "status":
                val = row.get("status", "")
            else:
                val = str(val) if val else ""

            rows.append(f"  <td>{val}</td>")
        rows.append("</tr>")

    rows.append("</table>")
    return "\n".join(rows)


def addArgs(subparsers):
    """Register the 'build' subcommand."""
    parser = subparsers.add_parser("build",
                                   help="Generate per-state HTML/CSV/text output pages")
    parser.set_defaults(func=build)


def build(args):
    """Generate per-state HTML/CSV/text output pages."""
    session = get_session()
    try:
        columns = _get_builder_columns()
        sections = sections_query(session, visible_only=True, with_gauge=True)
        states = all_state_names(session)

        print(f"Building pages for {len(sections)} sections across {len(states)} states")

        _build_and_store(session, sections, columns, "")

        for state in states:
            state_sections = sections_query(session, state_name=state, visible_only=True)
            if state_sections:
                _build_and_store(session, state_sections, columns, state)

        session.commit()
        print("Build complete")
    finally:
        session.close()


def _build_and_store(session, sections, columns, state: str):
    """Build and store CSV, text, and HTML for a state (or all)."""
    suffix = f"_{state}" if state else ""
    label = state or "all"

    logger.info("Building %s: %d sections", label, len(sections))

    csv_content = _build_csv(session, sections, columns, state)
    store_page(session, f"levels{suffix}.csv", PageAction.FILE, csv_content, "text/csv")

    text_content = _build_text(session, sections, columns, state)
    store_page(session, f"levels{suffix}.text", PageAction.FILE, text_content, "text/plain")

    html_content = _build_html(session, sections, columns, state)
    store_page(session, f"levels{suffix}.html", PageAction.PAGE, html_content, "text/html")
