"""Per-state levels table: row data, filter bar, HTML table."""

import html as html_mod
from datetime import UTC, datetime
from typing import Any

from kayak.config_data import load_builder_columns
from kayak.db.models import DataType, LatestGaugeObservation, Reach
from kayak.db.reaches import classify_level
from kayak.utils.class_tiers import parse_class_tiers
from kayak.utils.pubhash import encode as pubhash_encode
from kayak.web.build._shared import (
    DATA_EXPIRY_THRESHOLD,
    DATA_STALE_THRESHOLD,
)

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


def _get_builder_columns() -> list[dict]:
    cols = load_builder_columns()
    return sorted(cols, key=lambda c: c["sort_key"])


_ROW_DTYPE_ORDER: tuple[tuple[str, DataType], ...] = (
    ("flow", DataType.flow),
    ("gage", DataType.gauge),
    ("temperature", DataType.temperature),
    ("inflow", DataType.inflow),
)


def _apply_reach_observation(
    row: dict,
    reach: Reach,
    dtype_name: str,
    dtype: DataType,
    latest: LatestGaugeObservation,
) -> None:
    """Merge one (dtype, latest) into *row* — sets value, time, and level.

    ``inflow`` is remapped onto the ``flow`` column if no direct flow has
    been seen; if a flow row already exists, the inflow is dropped. Level
    classification uses flow thresholds for inflow (its display target).
    """
    display_name = dtype_name
    if dtype_name == "inflow":
        if "flow" in row:
            return
        display_name = "flow"
    row[display_name] = latest.value
    if "time" not in row or latest.observed_at > row["time"]:
        row["time"] = latest.observed_at
    if display_name not in ("flow", "gage"):
        return
    classify_dtype = DataType.flow if dtype == DataType.inflow else dtype
    level = classify_level(reach, classify_dtype, latest.value)
    if level:
        row[f"{display_name}_level"] = str(level)
        if "status" not in row:
            row["status"] = str(level)


def _apply_row_staleness(row: dict) -> None:
    """Flag *row* as expired/stale based on its newest observed_at."""
    obs_time = row["time"]
    if obs_time.tzinfo is None:
        obs_time = obs_time.replace(tzinfo=UTC)
    age = datetime.now(UTC) - obs_time
    if age > DATA_EXPIRY_THRESHOLD:
        row["expired"] = True
    elif age > DATA_STALE_THRESHOLD:
        row["stale"] = True


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
        # Render the cell as the 2-letter abbreviation (rightmost column on
        # index.html). Filter still uses full state names via data-state.
        "state": ", ".join(s.abbreviation or s.name for s in reach.states)
        if reach.states
        else "",
        "db_name": reach.name,
        "class": ", ".join(c.name for c in reach.classes) if reach.classes else "",
    }

    gauge = reach.gauge
    if gauge is None:
        return row
    if gauge.id in calculated_gauge_ids:
        row["is_estimated"] = True
    for dtype_name, dtype in _ROW_DTYPE_ORDER:
        latest = all_latest.get((gauge.id, dtype))
        if latest and latest.value is not None:
            _apply_reach_observation(row, reach, dtype_name, dtype, latest)
    if "time" in row:
        _apply_row_staleness(row)
    return row


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
        return f'<a href="/description.php?h={pubhash_encode(reach_id)}">{html_mod.escape(str(val))}{est}</a>'
    elif col["type"] == "flow":
        # The sparkline slot lives in the flow column regardless of which
        # series drives it — flow, inflow, or (fallback) gauge height. The
        # JS populates `<span class="spark">` elements by data-gid from
        # sparklines.json, so we emit the placeholder whenever a gauge
        # exists even if this reach's flow value itself is empty.
        gid_attr = f' data-gid="{gauge_id}"' if gauge_id else ""
        # aria-hidden on the outer placeholder so screen readers skip ~50
        # empty spans per page before JS resolves the SVG inside. The
        # numeric value to the left of the spark already conveys the
        # relevant info; the sparkline is decorative trend context.
        if isinstance(val, int | float):
            lvl = html_mod.escape(str(row["flow_level"])) if row.get("flow_level") else ""
            lvl_cls = f' class="level-{lvl}"' if lvl else ""
            return (
                f"<span{lvl_cls}>{val:,.0f}</span>"
                f'<span class="spark"{gid_attr} aria-hidden="true"></span>'
            )
        if gauge_id:
            return f'<span class="spark"{gid_attr} aria-hidden="true"></span>'
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
    state = ",".join(s.name for s in reach.states)
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
            f' data-href="/description.php?h={pubhash_encode(reach_id)}"'
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
    # huc6_code -> {huc8_code: display_name}. Using a dict (not a set of
    # tuples) collapses the case where the same huc8 appears across rows
    # with different `reach.basin` labels, which previously rendered as two
    # pills with the same code but different display names. On collision,
    # prefer the first non-numeric (named-basin) value we see; later named
    # values tie-break alphabetically, with the huc8 code as a stable last
    # resort if no reach supplied a named basin.
    huc6_to_huc8s: dict[str, dict[str, str]] = {}
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
            candidate = reach.basin or huc8
            bucket = huc6_to_huc8s.setdefault(huc6, {})
            existing = bucket.get(huc8)
            # Prefer a named basin (non-numeric) over the huc8 fallback,
            # then alphabetical among named candidates for determinism.
            if existing is None:
                bucket[huc8] = candidate
            elif existing == huc8 and candidate != huc8:
                # Existing is the numeric fallback; the new candidate is named.
                bucket[huc8] = candidate
            elif candidate != huc8 and existing != huc8 and candidate < existing:
                bucket[huc8] = candidate
        else:
            has_no_huc = True
    huc6_groups = [
        {
            "huc6": huc6,
            "name": huc6_names.get(huc6, huc6),
            "huc8s": sorted(huc8s.items()),
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
            f'    <summary>Watershed <span class="fg-count">{total}</span></summary>\n'
            f'    <div class="filter-pills" data-group="huc8">\n'
            f"      {toggle}\n"
            f"{body}\n"
            f"    </div>\n"
            f"  </details>"
        )

    groups: list[str] = []
    if is_all_page:
        # State emits as CSV on data-state (e.g. "Idaho,Nevada") for reaches
        # that cross state lines, so filters.js must split before matching.
        groups.append(group_html("state", "State", data["state"], lambda v: v, split_csv=True))
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
