"""Editing/submission routes (replaces submit.C, approve.C, authenticate.C)."""

from flask import Blueprint, abort, redirect, render_template_string, request, url_for

from kayak.db.engine import get_session
from kayak.db.info_db import authenticate_correction, submit_corrections
from kayak.db.models import MergedMaster

editing_bp = Blueprint("editing", __name__)

EDIT_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head><title>Edit {{ name }}</title></head>
<body>
<h1>Edit: {{ name }}</h1>
<form method="POST" action="/edit/{{ key }}/submit">
  <input type="hidden" name="hash_value" value="{{ key }}">
  <p>Your Name: <input type="text" name="user_name" required></p>
  <p>Email: <input type="email" name="email" required></p>
  <hr>
  {% for field, value in editable %}
  <p>{{ field }}: <input type="text" name="{{ field }}" value="{{ value or '' }}" size="60"></p>
  {% endfor %}
  <p><input type="submit" value="Submit Corrections"></p>
</form>
</body>
</html>
"""

SUBMITTED_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head><title>Corrections Submitted</title></head>
<body>
<h1>Corrections Submitted</h1>
<p>Your corrections for {{ name }} have been submitted for review.</p>
<p><a href="/">Back to main page</a></p>
</body>
</html>
"""


@editing_bp.route("/edit/<key>")
def edit(key: str):
    """Show the edit form for a station."""
    session = get_session()
    try:
        station = session.query(MergedMaster).filter_by(hash_value=key).first()
        if station is None:
            abort(404)

        name = station.display_name or key

        # Editable fields
        editable_fields = [
            "display_name", "gauge_location", "section", "state",
            "drainage", "region", "river_class", "length", "gradient",
            "elevation_lost", "season", "scenery", "features",
            "remoteness", "character", "difficulties", "low_flow",
            "high_flow", "optimal_flow", "bank_full", "flood_stage",
            "description", "notes",
        ]

        editable = []
        for field in editable_fields:
            val = getattr(station, field, None)
            editable.append((field, val))

        return render_template_string(
            EDIT_TEMPLATE, name=name, key=key, editable=editable
        )
    finally:
        session.close()


@editing_bp.route("/edit/<key>/submit", methods=["POST"])
def submit(key: str):
    """Process submitted corrections."""
    session = get_session()
    try:
        station = session.query(MergedMaster).filter_by(hash_value=key).first()
        if station is None:
            abort(404)

        user_name = request.form.get("user_name", "")
        email = request.form.get("email", "")

        corrections = {}
        for field_name in request.form:
            if field_name in ("hash_value", "user_name", "email"):
                continue
            val = request.form[field_name].strip()
            if val:
                corrections[field_name] = val

        if corrections:
            submit_corrections(session, key, user_name, email, corrections)
            session.commit()

        name = station.display_name or key
        return render_template_string(SUBMITTED_TEMPLATE, name=name)
    finally:
        session.close()


@editing_bp.route("/authenticate/<key>/<auth_key>")
def authenticate(key: str, auth_key: str):
    """Approve corrections via secret key."""
    session = get_session()
    try:
        if authenticate_correction(session, key, auth_key):
            session.commit()
            return "<h1>Corrections Approved</h1><p>The corrections have been applied.</p>"
        else:
            abort(404)
    finally:
        session.close()
