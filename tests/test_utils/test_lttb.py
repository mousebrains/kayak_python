"""Tests for kayak.utils.lttb — LTTB downsampling algorithm."""

from kayak.utils.lttb import downsample


def test_passthrough_below_threshold():
    """Data shorter than threshold is returned as-is."""
    data = [(1.0, 10.0), (2.0, 20.0), (3.0, 30.0)]
    result = downsample(data, 10)
    assert result == data


def test_passthrough_at_threshold():
    """Data at exactly threshold length is returned as-is."""
    data = [(float(i), float(i * 10)) for i in range(5)]
    result = downsample(data, 5)
    assert result == data


def test_threshold_too_small():
    """Threshold < 3 returns data as-is."""
    data = [(float(i), float(i)) for i in range(100)]
    result = downsample(data, 2)
    assert result == data


def test_downsamples_to_target_count():
    """Output has exactly threshold points."""
    data = [(float(i), float(i * i)) for i in range(1000)]
    result = downsample(data, 50)
    assert len(result) == 50


def test_preserves_first_and_last():
    """First and last points are always preserved."""
    data = [(float(i), float(i)) for i in range(100)]
    result = downsample(data, 10)
    assert result[0] == data[0]
    assert result[-1] == data[-1]


def test_preserves_peak():
    """A prominent peak is retained in the downsampled output."""
    data = [(float(i), 0.0) for i in range(100)]
    data[50] = (50.0, 1000.0)  # big spike
    result = downsample(data, 10)
    values = [y for _, y in result]
    assert 1000.0 in values


def test_empty_input():
    """Empty input returns empty output."""
    assert downsample([], 10) == []
