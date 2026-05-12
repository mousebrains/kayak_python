"""CSV and fixed-width text exports of the levels table.

Moved here from kayak/cli/build.py in Phase 5 of the build.py split
(docs/PLAN_build_split.md).
"""

import csv
import io
from datetime import datetime
from typing import Any

from kayak.db.models import DataType, LatestGaugeObservation, Reach
from kayak.web.build.levels import _get_row_data

_CSV_FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: str) -> str:
    """Prefix `'` if the string would be interpreted as a formula by Excel/
    Sheets/Numbers. RFC 4180 doesn't require this; it is a defense against
    ``levels.csv`` becoming an attack surface.

    Only string columns route through this; numeric values are emitted via
    format strings in ``_build_csv`` and never reach here.
    """
    if value and value.startswith(_CSV_FORMULA_PREFIX):
        return "'" + value
    return value


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
                formatted = f"{val:.1f}"
            elif isinstance(val, datetime):
                formatted = val.strftime("%Y-%m-%d %H:%M")
            else:
                formatted = _csv_safe(str(val))
            values.append(formatted)
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
