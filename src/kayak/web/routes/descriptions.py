"""Description routes (replaces CMD::description)."""

from flask import Blueprint, abort, render_template_string

from kayak.db.engine import get_session
from kayak.db.models import DescriptionField, MergedMaster

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


@descriptions_bp.route("/description/<key>")
def description(key: str):
    """Show detailed description page for a station."""
    session = get_session()
    try:
        station = session.query(MergedMaster).filter_by(hash_value=key).first()
        if station is None:
            abort(404)

        name = station.display_name or key

        # Get description field definitions
        desc_fields = (
            session.query(DescriptionField)
            .order_by(DescriptionField.sort_key)
            .all()
        )

        fields = []
        for df in desc_fields:
            if df.type == "noop":
                continue

            # Map column_name to model attribute
            col = df.column_name.lower()
            val = getattr(station, col, None)
            if val is None:
                # Try common mappings
                attr_map = {
                    "class": "river_class",
                    "nature": "character",
                    "runnumber": "run_number",
                    "pagenumber": "page_number",
                    "stationnumber": "station_number",
                    "username": "user_name",
                }
                mapped = attr_map.get(col)
                if mapped:
                    val = getattr(station, mapped, None)

            if val is None or str(val).strip() == "":
                continue

            if df.type == "DB":
                # Link to database view
                val = f'<a href="/view/{key}">{val}</a>'
            elif df.type == "URL":
                val = f'<a href="/plot/flow/{key}">{val}</a>'
            elif df.type == "calc":
                val = str(val)
            elif df.type == "ptxt":
                val = f"<pre>{val}</pre>"

            fields.append({
                "prefix": df.prefix,
                "value": val,
                "suffix": df.suffix,
            })

        return render_template_string(DESC_TEMPLATE, name=name, fields=fields)
    finally:
        session.close()
