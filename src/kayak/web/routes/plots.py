"""Plot routes (replaces CMD::plot, svg.C, png.C)."""

from flask import Blueprint, abort, make_response, request

from kayak.db.data_db import get_measurements
from kayak.db.engine import get_session
from kayak.db.info_db import display_name
from kayak.db.models import DataType
from kayak.plotting.timeseries import generate_plot

plots_bp = Blueprint("plots", __name__)


def _plot_response(key: str, data_type: DataType, title_suffix: str, fmt: str = "svg"):
    """Generate a time-series plot for a station."""
    session = get_session()
    try:
        name = display_name(session, key) or key
        records = get_measurements(session, key, data_type)

        if not records:
            # Try using key as db_name directly
            from kayak.db.models import MergedMaster
            station = session.query(MergedMaster).filter_by(hash_value=key).first()
            if station and station.db_name:
                records = get_measurements(session, station.db_name, data_type)
                name = station.display_name or station.db_name

        if not records:
            abort(404)

        times = [r.time for r in records]
        values = [r.value for r in records]
        title = f"{name} — {title_suffix}"

        img_bytes = generate_plot(times, values, title, title_suffix, fmt=fmt)

        mime = "image/svg+xml" if fmt == "svg" else "image/png"
        resp = make_response(img_bytes)
        resp.headers["Content-Type"] = mime
        resp.headers["Cache-Control"] = "max-age=300"
        return resp
    finally:
        session.close()


@plots_bp.route("/plot/flow/<key>")
def flow_plot(key: str):
    fmt = request.args.get("fmt", "svg")
    return _plot_response(key, DataType.FLOW, "Flow (CFS)", fmt)


@plots_bp.route("/plot/gage/<key>")
def gage_plot(key: str):
    fmt = request.args.get("fmt", "svg")
    return _plot_response(key, DataType.GAGE, "Gauge (Ft)", fmt)


@plots_bp.route("/plot/temp/<key>")
def temp_plot(key: str):
    fmt = request.args.get("fmt", "svg")
    return _plot_response(key, DataType.TEMPERATURE, "Temperature (F)", fmt)
