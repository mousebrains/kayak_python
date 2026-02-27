"""Description routes (replaces CMD::description)."""

from flask import Blueprint, abort, render_template_string

from kayak.config_data import load_description_fields
from kayak.db.engine import get_session
from kayak.db.info_db import get_section

descriptions_bp = Blueprint("descriptions", __name__)

DESC_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head><title>{{ name }} - Description</title></head>
<body>
<h1>{{ name }}</h1>
<table>
{% for field in fields %}
<tr>
  <td><strong>{{ field.prefix }}</strong></td>
  <td>{{ field.value }}{{ field.suffix|safe }}</td>
</tr>
{% endfor %}
</table>
<p><a href="/">Back to main page</a></p>
</body>
</html>
"""


@descriptions_bp.route("/description/<int:section_id>")
def description(section_id: int):
    """Show detailed description page for a section."""
    session = get_session()
    try:
        section = get_section(session, section_id)
        if section is None:
            abort(404)

        name = section.display_name or section.name

        # Load field definitions from YAML
        desc_field_defs = load_description_fields()

        # Build attribute map from section + gauge
        attr_map: dict[str, str | int | float | None] = {}
        for attr_name in dir(section):
            if not attr_name.startswith("_"):
                val = getattr(section, attr_name, None)
                if isinstance(val, (str, int, float)):
                    attr_map[attr_name] = val

        gauge = section.gauge
        if gauge:
            attr_map["gauge_location"] = gauge.location or ""
            attr_map["usgs_id"] = gauge.usgs_id or ""
            attr_map["nwsli_id"] = gauge.nwsli_id or ""
            attr_map["geos_id"] = gauge.geos_id or ""
            attr_map["station_number"] = gauge.station_id or ""
            attr_map["bank_full"] = gauge.bank_full
            attr_map["flood_stage"] = gauge.flood_stage

        if section.states:
            attr_map["state"] = ", ".join(s.name for s in section.states)
        if section.classes:
            attr_map["class"] = ", ".join(c.name for c in section.classes)

        fields = []
        for df in sorted(desc_field_defs, key=lambda d: d["sort_key"]):
            if df["type"] == "noop":
                continue

            col = df["column"]
            val = attr_map.get(col)

            if val is None or str(val).strip() == "":
                continue

            val = str(val)

            if df["type"] == "DB":
                val = f'<a href="/view/{section_id}">{val}</a>'
            elif df["type"] == "URL":
                val = f'<a href="/plot/flow/{section_id}">{val}</a>'
            elif df["type"] == "ptxt":
                val = f"<pre>{val}</pre>"

            fields.append({
                "prefix": df["prefix"],
                "value": val,
                "suffix": df["suffix"],
            })

        return render_template_string(DESC_TEMPLATE, name=name, fields=fields)
    finally:
        session.close()
