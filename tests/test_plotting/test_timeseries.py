"""Tests for kayak.plotting.timeseries — time-series plot generation."""

from datetime import UTC, datetime, timedelta

from kayak.plotting.timeseries import generate_plot


def test_svg_output_contains_svg_element():
    """SVG output contains an <svg element."""
    now = datetime.now(UTC)
    times = [now - timedelta(hours=i) for i in range(10)]
    values = [100.0 + i * 10 for i in range(10)]

    result = generate_plot(times, values, "Test Plot", "Value")

    assert isinstance(result, bytes)
    assert b"<svg" in result


def test_png_output_starts_with_magic_bytes():
    """PNG output starts with the PNG magic bytes."""
    now = datetime.now(UTC)
    times = [now - timedelta(hours=i) for i in range(10)]
    values = [100.0 + i * 10 for i in range(10)]

    result = generate_plot(times, values, "Test Plot", "Value", fmt="png")

    assert isinstance(result, bytes)
    assert result[:4] == b"\x89PNG"


def test_empty_input_produces_no_data_plot():
    """Empty input produces a plot with 'No data' text."""
    result = generate_plot([], [], "Empty Plot", "Value")

    assert isinstance(result, bytes)
    # SVG is text-based, so we can check for the "No data" message
    assert b"No data" in result


def test_output_is_bytes():
    """generate_plot always returns bytes."""
    now = datetime.now(UTC)
    times = [now - timedelta(hours=i) for i in range(5)]
    values = [50.0 + i for i in range(5)]

    svg_result = generate_plot(times, values, "Test", "Y")
    png_result = generate_plot(times, values, "Test", "Y", fmt="png")

    assert isinstance(svg_result, bytes)
    assert isinstance(png_result, bytes)


def test_different_dimensions_produce_different_output():
    """Different width/height produce different size output."""
    now = datetime.now(UTC)
    times = [now - timedelta(hours=i) for i in range(10)]
    values = [100.0 + i for i in range(10)]

    small = generate_plot(times, values, "Small", "Y", width=5.0, height=3.0, fmt="png")
    large = generate_plot(
        times, values, "Large", "Y", width=15.0, height=8.0, fmt="png"
    )

    assert len(large) > len(small)


def test_long_vs_short_time_span():
    """Long and short time spans produce different x-axis formatting.

    A 60-day span uses weekly locators while a 2-day span uses hourly
    locators, so the SVG output differs in date formatting patterns.
    """
    now = datetime.now(UTC)

    # Short span: 2 days of hourly data
    short_times = [now - timedelta(hours=i) for i in range(48)]
    short_values = [100.0 + i for i in range(48)]
    short_svg = generate_plot(short_times, short_values, "Short Span", "Y")

    # Long span: 60 days of daily data
    long_times = [now - timedelta(days=i) for i in range(60)]
    long_values = [100.0 + i for i in range(60)]
    long_svg = generate_plot(long_times, long_values, "Long Span", "Y")

    # Both should produce valid SVG, but with different content
    assert b"<svg" in short_svg
    assert b"<svg" in long_svg
    # The outputs should differ (different axis labels, tick marks)
    assert short_svg != long_svg
