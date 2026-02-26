"""Plot routes (replaces CMD::plot, svg.C, png.C)."""

from flask import Blueprint, abort, make_response, request

from kayak.db.data_db import get_observations
from kayak.db.engine import get_session
from kayak.db.info_db import get_section
from kayak.db.models import DataType, GaugeSource
from kayak.plotting.timeseries import generate_plot

plots_bp = Blueprint("plots", __name__)


def _plot_response(section_id: int, data_type: DataType, title_suffix: str, fmt: str = "svg"):
    """Generate a time-series plot for a section's gauge."""
    session = get_session()
    try:
        section = get_section(session, section_id)
        if section is None:
            abort(404)

        name = section.display_name or section.name
        gauge = section.gauge

        records = []
        if gauge:
            gs = session.query(GaugeSource).filter(
                GaugeSource.gauge_id == gauge.id
            ).first()
            if gs:
                records = get_observations(session, gs.source_id, data_type)

        if not records:
            abort(404)

        times = [r.observed_at for r in records]
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


@plots_bp.route("/plot/flow/<int:section_id>")
def flow_plot(section_id: int):
    fmt = request.args.get("fmt", "svg")
    return _plot_response(section_id, DataType.flow, "Flow (CFS)", fmt)


@plots_bp.route("/plot/gage/<int:section_id>")
def gage_plot(section_id: int):
    fmt = request.args.get("fmt", "svg")
    return _plot_response(section_id, DataType.gauge, "Gauge (Ft)", fmt)


@plots_bp.route("/plot/temp/<int:section_id>")
def temp_plot(section_id: int):
    fmt = request.args.get("fmt", "svg")
    return _plot_response(section_id, DataType.temperature, "Temperature (F)", fmt)
