"""Editing/submission routes (replaces submit.C, approve.C, authenticate.C).

Note: The old Master/Correction approval workflow is replaced with direct
Section editing. In the new schema, edits are applied directly to the
Section model.
"""

from flask import Blueprint, abort, render_template_string, request

from kayak.db.engine import get_session
from kayak.db.info_db import get_section

editing_bp = Blueprint("editing", __name__)

EDIT_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head><title>Edit {{ name }}</title></head>
<body>
<h1>Edit: {{ name }}</h1>
<form method="POST" action="/edit/{{ section_id }}/submit">
  <input type="hidden" name="section_id" value="{{ section_id }}">
  {% for field, value in editable %}
  <p>{{ field }}: <input type="text" name="{{ field }}" value="{{ value or '' }}" size="60"></p>
  {% endfor %}
  <p><input type="submit" value="Submit Changes"></p>
</form>
</body>
</html>
"""

SUBMITTED_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head><title>Changes Saved</title></head>
<body>
<h1>Changes Saved</h1>
<p>Your changes for {{ name }} have been saved.</p>
<p><a href="/">Back to main page</a></p>
</body>
</html>
"""


@editing_bp.route("/edit/<int:section_id>")
def edit(section_id: int):
    """Show the edit form for a section."""
    session = get_session()
    try:
        section = get_section(session, section_id)
        if section is None:
            abort(404)

        name = section.display_name or section.name

        editable_fields = [
            "display_name", "sort_name", "description", "difficulties",
            "basin", "region", "length", "gradient", "elevation_lost",
            "season", "scenery", "features", "remoteness", "nature",
            "watershed_type", "optimal_flow", "notes",
        ]

        editable = []
        for field in editable_fields:
            val = getattr(section, field, None)
            editable.append((field, val))

        return render_template_string(
            EDIT_TEMPLATE, name=name, section_id=section_id, editable=editable
        )
    finally:
        session.close()


@editing_bp.route("/edit/<int:section_id>/submit", methods=["POST"])
def submit(section_id: int):
    """Process submitted section edits."""
    session = get_session()
    try:
        section = get_section(session, section_id)
        if section is None:
            abort(404)

        for field_name in request.form:
            if field_name == "section_id":
                continue
            val = request.form[field_name].strip()
            if val and hasattr(section, field_name):
                # Convert numeric fields
                if field_name in ("length", "gradient", "elevation_lost", "optimal_flow"):
                    try:
                        val = float(val)
                    except ValueError:
                        continue
                setattr(section, field_name, val)

        session.commit()

        name = section.display_name or section.name
        return render_template_string(SUBMITTED_TEMPLATE, name=name)
    finally:
        session.close()
