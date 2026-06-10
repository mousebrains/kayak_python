"""Sparkline SVG generation for the levels page."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from kayak.db.gauges import get_bulk_gauge_observations
from kayak.db.models import DataType, Observation, Reach
from kayak.utils.lttb import downsample, running_median
from kayak.web.build._shared import BRAND_COLOR

# Sparkline rendering
SPARKLINE_MEDIAN_WINDOW_SECS = 3 * 3600  # 3-hour running median window
SPARKLINE_DOWNSAMPLE_POINTS = 60  # Target points after LTTB downsampling
SPARKLINE_STROKE_WIDTH = "1.5"
SPARKLINE_COLOR = BRAND_COLOR  # dataset brand color (site.yaml); engine blue by default

SPARKLINE_OBSERVATION_WINDOW = timedelta(hours=48)

# Sparkline series-selection freshness: a series is considered "current" if
# its most recent observation is within this window. Used to decide whether
# flow/inflow is current enough to plot, or to fall back to gauge height.
SPARKLINE_CURRENT_WINDOW = timedelta(hours=6)


def _select_sparkline_series(
    session: Session, gauge_ids: list[int]
) -> dict[int, list[Observation]]:
    """Choose which data-type series drives each gauge's sparkline.

    Per-gauge preference: flow → inflow → gauge, taking whichever has a
    latest observation within ``SPARKLINE_CURRENT_WINDOW``. If flow or
    inflow has only stale points, we fall through to gauge-height rather
    than draw a multi-day-old flow line. Stored values are naive-UTC in
    SQLite, so we compare against ``datetime.now(UTC)`` after stamping UTC.
    """
    since_48h = datetime.now(UTC) - SPARKLINE_OBSERVATION_WINDOW
    current_cutoff = datetime.now(UTC) - SPARKLINE_CURRENT_WINDOW
    flow_obs = get_bulk_gauge_observations(session, gauge_ids, DataType.flow, since_48h)
    inflow_obs = get_bulk_gauge_observations(session, gauge_ids, DataType.inflow, since_48h)
    gauge_obs = get_bulk_gauge_observations(session, gauge_ids, DataType.gauge, since_48h)

    def _is_current(obs: list[Observation] | None) -> bool:
        if not obs:
            return False
        latest = max(o.observed_at for o in obs)
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=UTC)
        return latest >= current_cutoff

    selected: dict[int, list[Observation]] = {}
    for gid in gauge_ids:
        for series in (flow_obs.get(gid), inflow_obs.get(gid), gauge_obs.get(gid)):
            if _is_current(series):
                selected[gid] = series  # type: ignore[assignment]
                break
    return selected


def _sparkline_svg_from_records(
    records: list[Observation],
    width: int = 80,
    height: int = 20,
) -> str:
    """Render the sparkline SVG from raw observations. Empty if insufficient data."""
    if len(records) < 3:
        return ""

    pairs = sorted(
        [(r.observed_at.timestamp(), r.value) for r in records if r.value is not None],
        key=lambda p: p[0],
    )
    if len(pairs) < 3:
        return ""

    pairs = running_median(pairs, window_seconds=SPARKLINE_MEDIAN_WINDOW_SECS)
    pairs = downsample(pairs, SPARKLINE_DOWNSAMPLE_POINTS)

    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    x_min, x_max = xs[0], xs[-1]
    y_min, y_max = min(ys), max(ys)

    x_range = x_max - x_min or 1
    y_range = y_max - y_min or 1

    points = " ".join(
        f"{int((x - x_min) / x_range * width)},{int(height - (y - y_min) / y_range * height)}"
        for x, y in pairs
    )

    return (
        f'<svg class="spark" width="{width}" height="{height}" viewBox="0 0 {width} {height}" aria-hidden="true">'
        f'<polyline fill="none" stroke="{SPARKLINE_COLOR}" stroke-width="{SPARKLINE_STROKE_WIDTH}" points="{points}"/>'
        f"</svg>"
    )


def _build_sparkline(
    reach: Reach,
    sparkline_obs: dict[int, list[Observation]],
    width: int = 80,
    height: int = 20,
) -> str:
    """Generate a tiny inline SVG sparkline from pre-loaded gauge observation data."""
    gauge = reach.gauge
    if not gauge:
        return ""
    return _sparkline_svg_from_records(sparkline_obs.get(gauge.id, []), width, height)
