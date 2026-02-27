"""Time-series plotting with Matplotlib (replaces Canvas/Plot C++ hierarchy).

The C++ codebase used ~600 lines across 12 files (Canvas.C/H, SVGCanvas,
PNGCanvas, BitMapCanvas, Plot.C/H, MakePlot.C/H, etc.). Matplotlib
replaces all of this with a single module.
"""

from __future__ import annotations

import io
from datetime import datetime

import matplotlib

matplotlib.use("Agg")  # Non-interactive backend

import matplotlib.dates as mdates
import matplotlib.pyplot as plt


def generate_plot(
    times: list[datetime],
    values: list[float],
    title: str,
    y_label: str,
    fmt: str = "svg",
    width: float = 10.0,
    height: float = 4.0,
) -> bytes:
    """Generate a time-series plot and return as bytes.

    Args:
        times: X-axis datetime values
        values: Y-axis numeric values
        title: Plot title
        y_label: Y-axis label
        fmt: Output format ("svg" or "png")
        width: Figure width in inches
        height: Figure height in inches

    Returns:
        Plot as bytes in the requested format
    """
    fig, ax = plt.subplots(figsize=(width, height))

    try:
        # Sort by time
        paired = sorted(zip(times, values, strict=True), key=lambda x: x[0])
        if not paired:
            return _empty_plot(title, fmt, fig, ax)

        t, v = zip(*paired, strict=True)

        # Plot the data
        ax.plot(t, v, color="#2060A0", linewidth=1.0)

        # Configure axes
        ax.set_title(title, fontsize=12)
        ax.set_ylabel(y_label, fontsize=10)

        # Date formatting on x-axis
        if len(t) > 1:
            span = (t[-1] - t[0]).total_seconds() / 86400
            if span > 30:
                ax.xaxis.set_major_locator(mdates.WeekdayLocator())
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
            elif span > 7:
                ax.xaxis.set_major_locator(mdates.DayLocator())
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
            else:
                ax.xaxis.set_major_locator(mdates.HourLocator(interval=6))
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))

        ax.tick_params(axis="x", rotation=45, labelsize=8)
        ax.tick_params(axis="y", labelsize=9)

        # Grid
        ax.grid(True, color="#C0D0E0", linewidth=0.5, alpha=0.7)
        ax.set_axisbelow(True)

        # Y-axis range padding
        if v:
            ymin, ymax = min(v), max(v)
            margin = (ymax - ymin) * 0.05 if ymax != ymin else 1.0
            ax.set_ylim(ymin - margin, ymax + margin)

        # Generation timestamp
        now = datetime.now()
        ax.annotate(
            f"Generated {now.strftime('%Y-%m-%d %H:%M')}",
            xy=(1, 0), xycoords="axes fraction",
            fontsize=6, color="gray",
            ha="right", va="top",
        )

        fig.tight_layout()

        # Render to bytes
        buf = io.BytesIO()
        fig.savefig(buf, format=fmt, dpi=100 if fmt == "png" else None)
        buf.seek(0)
        return buf.read()

    finally:
        plt.close(fig)


def _empty_plot(title: str, fmt: str, fig, ax) -> bytes:
    """Generate an empty plot with a 'No data' message."""
    ax.set_title(title)
    ax.text(
        0.5, 0.5, "No data available",
        transform=ax.transAxes, ha="center", va="center",
        fontsize=14, color="gray",
    )
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    buf = io.BytesIO()
    fig.savefig(buf, format=fmt)
    buf.seek(0)
    return buf.read()
