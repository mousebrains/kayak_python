"""View routes (replaces CMD::view)."""

from flask import Blueprint, abort, render_template_string

from kayak.db.data_db import get_latest
from kayak.db.engine import get_session
from kayak.db.info_db import get_section
from kayak.db.models import DataType, GaugeSource

views_bp = Blueprint("views", __name__)

VIEW_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head><title>{{ name }} - River Data</title></head>
<body>
<h1>{{ name }}</h1>
<table border="1">
<tr><th>Type</th><th>Latest Value</th><th>Time</th><th>Change/hr</th></tr>
{% for dtype, latest in data.items() %}
{% if latest %}
<tr>
  <td>{{ dtype }}</td>
  <td>{{ "%.2f"|format(latest.value) if latest.value is not none else "N/A" }}</td>
  <td>{{ latest.observed_at.strftime("%Y-%m-%d %H:%M") if latest.observed_at else "N/A" }}</td>
  <td>{{ "%.2f"|format(latest.delta_per_hour) if latest.delta_per_hour is not none else "N/A" }}</td>
</tr>
{% endif %}
{% endfor %}
</table>
</body>
</html>
"""


@views_bp.route("/view/<int:section_id>")
def view(section_id: int):
    """View current data for a section."""
    session = get_session()
    try:
        section = get_section(session, section_id)
        if section is None:
            abort(404)

        name = section.display_name or section.name
        gauge = section.gauge

        data = {}
        if gauge:
            gs = session.query(GaugeSource).filter(
                GaugeSource.gauge_id == gauge.id
            ).first()
            if gs:
                for dtype in DataType:
                    latest = get_latest(session, gs.source_id, dtype)
                    if latest and latest.value is not None:
                        data[dtype.value] = latest

        return render_template_string(VIEW_TEMPLATE, name=name, data=data)
    finally:
        session.close()
