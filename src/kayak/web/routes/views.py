"""View routes (replaces CMD::view)."""

from flask import Blueprint, abort, render_template_string

from kayak.db.data_db import get_latest, get_measurements
from kayak.db.engine import get_session
from kayak.db.models import DataType, MergedMaster

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
  <td>{{ latest.time.strftime("%Y-%m-%d %H:%M") if latest.time else "N/A" }}</td>
  <td>{{ "%.2f"|format(latest.delta) if latest.delta is not none else "N/A" }}</td>
</tr>
{% endif %}
{% endfor %}
</table>
</body>
</html>
"""


@views_bp.route("/view/<key>")
def view(key: str):
    """View current data for a station."""
    session = get_session()
    try:
        station = session.query(MergedMaster).filter_by(hash_value=key).first()
        if station is None:
            abort(404)

        db_name = station.db_name or key
        name = station.display_name or db_name

        data = {}
        for dtype in DataType:
            latest = get_latest(session, db_name, dtype)
            if latest and latest.value is not None:
                data[dtype.value] = latest

        return render_template_string(VIEW_TEMPLATE, name=name, data=data)
    finally:
        session.close()
