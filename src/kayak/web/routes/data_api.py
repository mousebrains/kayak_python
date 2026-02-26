"""Raw data API routes (replaces data.C)."""

import json
from datetime import datetime, timezone

from flask import Blueprint, abort, jsonify, request

from kayak.db.data_db import get_latest, get_measurements
from kayak.db.engine import get_session
from kayak.db.models import DataType, MergedMaster

data_api_bp = Blueprint("data_api", __name__, url_prefix="/api")


@data_api_bp.route("/data/<key>/<data_type>")
def get_data(key: str, data_type: str):
    """Return measurement data as JSON."""
    session = get_session()
    try:
        try:
            dtype = DataType(data_type)
        except ValueError:
            abort(400, description=f"Unknown data type: {data_type}")

        # Resolve hash to db_name
        station = session.query(MergedMaster).filter_by(hash_value=key).first()
        db_name = station.db_name if station else key

        days = int(request.args.get("days", 60))
        since = datetime.now(timezone.utc)
        from datetime import timedelta
        since = since - timedelta(days=days)

        records = get_measurements(session, db_name, dtype, since=since)

        return jsonify({
            "station": db_name,
            "type": data_type,
            "count": len(records),
            "data": [
                {
                    "time": r.time.isoformat(),
                    "value": r.value,
                }
                for r in records
            ],
        })
    finally:
        session.close()


@data_api_bp.route("/latest/<key>")
def get_latest_data(key: str):
    """Return latest values for all data types."""
    session = get_session()
    try:
        station = session.query(MergedMaster).filter_by(hash_value=key).first()
        db_name = station.db_name if station else key
        name = station.display_name if station else key

        result = {"station": db_name, "name": name, "types": {}}

        for dtype in DataType:
            latest = get_latest(session, db_name, dtype)
            if latest and latest.value is not None:
                result["types"][dtype.value] = {
                    "value": latest.value,
                    "time": latest.time.isoformat() if latest.time else None,
                    "delta": latest.delta,
                    "prev_value": latest.prev_value,
                    "prev_time": latest.prev_time.isoformat() if latest.prev_time else None,
                }

        return jsonify(result)
    finally:
        session.close()
