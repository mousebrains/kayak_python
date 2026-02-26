"""Builder command (replaces builder.C + Display.C).

Generates per-state HTML, CSV, and text output pages with current
river level data.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone

import click

from kayak.db.data_db import get_latest
from kayak.db.engine import get_session
from kayak.db.info_db import all_states, master_query
from kayak.db.models import (
    BuilderColumn,
    DataType,
    MergedMaster,
    PageAction,
)
from kayak.db.page_db import store_page

logger = logging.getLogger(__name__)


def _get_builder_columns(session) -> list[BuilderColumn]:
    """Get builder column definitions sorted by sort_key."""
    return (
        session.query(BuilderColumn)
        .order_by(BuilderColumn.sort_key)
        .all()
    )


def _get_row_data(session, record: MergedMaster) -> dict:
    """Build a data dict for one river station."""
    row = {
        "hash_value": record.hash_value,
        "display_name": record.display_name or "",
        "gauge_location": record.gauge_location or "",
        "drainage": record.drainage or "",
        "class": record.river_class or "",
        "state": record.state or "",
        "db_name": record.db_name or "",
        "calc_expr": record.calc_expr or "",
        "low_flow": record.low_flow or "",
        "high_flow": record.high_flow or "",
        "class_flow": record.class_flow or "",
    }

    db_name = record.db_name
    if db_name:
        for dtype_name, dtype in [
            ("flow", DataType.FLOW),
            ("gage", DataType.GAGE),
            ("temperature", DataType.TEMPERATURE),
        ]:
            latest = get_latest(session, db_name, dtype)
            if latest and latest.value is not None:
                row[dtype_name] = latest.value
                row["time"] = latest.time
                if latest.delta is not None:
                    # Status: rising/falling/stable
                    if abs(latest.delta) < 0.5:
                        row["status"] = "stable"
                    elif latest.delta > 0:
                        row["status"] = "rising"
                    else:
                        row["status"] = "falling"

    return row


def _build_csv(session, records, columns, state_name: str) -> str:
    """Generate CSV output for a set of records."""
    output = io.StringIO()
    writer = csv.writer(output)

    # Header row
    headers = [c.name_text for c in columns if "c" in c.use and c.type != "noop"]
    writer.writerow(headers)

    for record in records:
        row = _get_row_data(session, record)
        values = []
        for col in columns:
            if "c" not in col.use or col.type == "noop":
                continue
            val = row.get(col.field, "")
            if isinstance(val, float):
                val = f"{val:.1f}"
            elif isinstance(val, datetime):
                val = val.strftime("%Y-%m-%d %H:%M")
            values.append(str(val))
        writer.writerow(values)

    return output.getvalue()


def _build_text(session, records, columns, state_name: str) -> str:
    """Generate fixed-width text output for a set of records."""
    lines = []

    # Header
    header = ""
    for col in columns:
        if "t" not in col.use or col.type == "noop":
            continue
        header += col.name_text.ljust(col.length)
    lines.append(header)
    lines.append("-" * len(header))

    for record in records:
        row = _get_row_data(session, record)
        line = ""
        for col in columns:
            if "t" not in col.use or col.type == "noop":
                continue
            val = row.get(col.field, "")
            if isinstance(val, float):
                val = f"{val:.1f}"
            elif isinstance(val, datetime):
                val = val.strftime("%m/%d %H:%M")
            line += str(val)[:col.length].ljust(col.length)
        lines.append(line)

    return "\n".join(lines)


def _build_html(session, records, columns, state_name: str) -> str:
    """Generate HTML table output for a set of records."""
    rows = []
    rows.append("<table class='levels'>")

    # Header
    rows.append("<tr>")
    for col in columns:
        if "h" not in col.use or col.type == "noop":
            continue
        rows.append(f"  <th>{col.name_html}</th>")
    rows.append("</tr>")

    for record in records:
        row = _get_row_data(session, record)
        hash_val = record.hash_value
        rows.append("<tr>")
        for col in columns:
            if "h" not in col.use or col.type == "noop":
                continue
            val = row.get(col.field, "")

            if col.type == "name":
                val = f'<a href="?D={hash_val}">{val}</a>'
            elif col.type == "flow" and isinstance(val, (int, float)):
                val = f'<a href="?f={hash_val}">{val:.0f}</a>'
            elif col.type == "gage" and isinstance(val, (int, float)):
                val = f'<a href="?g={hash_val}">{val:.2f}</a>'
            elif col.type == "temp" and isinstance(val, (int, float)):
                val = f'<a href="?t={hash_val}">{val:.0f}</a>'
            elif col.type == "date" and isinstance(val, datetime):
                val = val.strftime("%m/%d %H:%M")
            elif col.type == "status":
                val = row.get("status", "")
            else:
                val = str(val) if val else ""

            rows.append(f"  <td>{val}</td>")
        rows.append("</tr>")

    rows.append("</table>")
    return "\n".join(rows)


@click.command("build")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output")
def build_cmd(verbose):
    """Generate per-state HTML/CSV/text output pages."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    session = get_session()
    try:
        columns = _get_builder_columns(session)
        records = master_query(
            session, "no_show is null and db_name is not null"
        )
        states = all_states(session)

        click.echo(f"Building pages for {len(records)} stations across {len(states)} states")

        # Build all-states pages
        _build_and_store(session, records, columns, "", verbose)

        # Build per-state pages
        for state in states:
            state_records = [r for r in records if r.state and state in r.state]
            if state_records:
                _build_and_store(session, state_records, columns, state, verbose)

        session.commit()
        click.echo("Build complete")
    finally:
        session.close()


def _build_and_store(session, records, columns, state: str, verbose: bool):
    """Build and store CSV, text, and HTML for a state (or all)."""
    suffix = f"_{state}" if state else ""
    label = state or "all"

    if verbose:
        click.echo(f"  Building {label}: {len(records)} records")

    csv_content = _build_csv(session, records, columns, state)
    store_page(session, f"levels{suffix}.csv", PageAction.FILE, csv_content, "text/csv")

    text_content = _build_text(session, records, columns, state)
    store_page(session, f"levels{suffix}.text", PageAction.FILE, text_content, "text/plain")

    html_content = _build_html(session, records, columns, state)
    store_page(session, f"levels{suffix}.html", PageAction.PAGE, html_content, "text/html")
