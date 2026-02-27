"""Raw data API routes (replaces data.C)."""

from datetime import UTC, datetime, timedelta

from flask import Blueprint, abort, jsonify, request

from kayak.db.data_db import get_latest, get_observations
from kayak.db.engine import get_session
from kayak.db.info_db import get_primary_source_id, get_section
from kayak.db.models import DataType

data_api_bp = Blueprint("data_api", __name__, url_prefix="/api")


@data_api_bp.route("/data/<int:section_id>/<data_type>")
def get_data(section_id: int, data_type: str):
    """Return observation data as JSON."""
    session = get_session()
    try:
        try:
            dtype = DataType(data_type)
        except ValueError:
            abort(400, description=f"Unknown data type: {data_type}")

        section = get_section(session, section_id)
        if section is None:
            abort(404)

        gauge = section.gauge
        if gauge is None:
            return jsonify({"section": section.name, "type": data_type, "count": 0, "data": []})

        source_id = get_primary_source_id(session, gauge.id)
        if source_id is None:
            return jsonify({"section": section.name, "type": data_type, "count": 0, "data": []})

        days = int(request.args.get("days", 60))
        since = datetime.now(UTC) - timedelta(days=days)

        records = get_observations(session, source_id, dtype, since=since)

        return jsonify({
            "section": section.name,
            "type": data_type,
            "count": len(records),
            "data": [
                {
                    "time": r.observed_at.isoformat(),
                    "value": r.value,
                }
                for r in records
            ],
        })
    finally:
        session.close()


@data_api_bp.route("/latest/<int:section_id>")
def get_latest_data(section_id: int):
    """Return latest values for all data types."""
    session = get_session()
    try:
        section = get_section(session, section_id)
        if section is None:
            abort(404)

        name = section.display_name or section.name

        types: dict[str, dict[str, object]] = {}
        result: dict[str, object] = {"section": section.name, "name": name, "types": types}

        gauge = section.gauge
        if gauge:
            source_id = get_primary_source_id(session, gauge.id)
            if source_id:
                for dtype in DataType:
                    latest = get_latest(session, source_id, dtype)
                    if latest and latest.value is not None:
                        types[dtype.value] = {
                            "value": latest.value,
                            "time": latest.observed_at.isoformat() if latest.observed_at else None,
                            "delta_per_hour": latest.delta_per_hour,
                            "prev_value": latest.prev_value,
                            "prev_time": latest.prev_observed_at.isoformat() if latest.prev_observed_at else None,
                        }

        return jsonify(result)
    finally:
        session.close()
