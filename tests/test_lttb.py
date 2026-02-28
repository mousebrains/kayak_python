"""Tests for LTTB downsampling and running median."""

from kayak.utils.lttb import downsample, running_median


def test_running_median_smooths_spike():
    """A single spike should be smoothed away by the median."""
    # Flat signal at 100 with a spike at t=5
    data = [(float(i), 100.0) for i in range(10)]
    data[5] = (5.0, 999.0)  # spike
    result = running_median(data, window_seconds=4.0)
    # The spike at t=5 should be smoothed — window covers t=3..7 (5 points)
    assert len(result) == 10
    assert result[5][1] == 100.0  # median of [100,100,999,100,100] = 100


def test_running_median_preserves_step():
    """A real step change should be preserved."""
    data = [(float(i), 0.0 if i < 5 else 100.0) for i in range(10)]
    result = running_median(data, window_seconds=2.0)
    # Points well before and after the step should remain unchanged
    assert result[0][1] == 0.0
    assert result[9][1] == 100.0


def test_running_median_single_point():
    """Single point should be returned as-is."""
    data = [(1.0, 42.0)]
    result = running_median(data, window_seconds=10.0)
    assert result == [(1.0, 42.0)]


def test_running_median_empty():
    """Empty input should return empty list."""
    assert running_median([], window_seconds=10.0) == []


def test_running_median_two_points():
    """Two points should return median of whatever falls in the window."""
    data = [(0.0, 10.0), (1.0, 20.0)]
    result = running_median(data, window_seconds=4.0)
    assert len(result) == 2
    # Both points are within each other's window, median of [10,20] = 15
    assert result[0][1] == 15.0
    assert result[1][1] == 15.0


def test_running_median_narrow_window():
    """A window smaller than the spacing should return original values."""
    data = [(0.0, 10.0), (10.0, 20.0), (20.0, 30.0)]
    result = running_median(data, window_seconds=1.0)
    # Each point only sees itself
    assert result == [(0.0, 10.0), (10.0, 20.0), (20.0, 30.0)]


def test_downsample_passthrough():
    """Fewer points than threshold returns data unchanged."""
    data = [(1.0, 10.0), (2.0, 20.0)]
    assert downsample(data, 5) == data
