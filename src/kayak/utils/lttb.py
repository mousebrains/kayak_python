"""Largest Triangle Three Buckets (LTTB) downsampling algorithm.

Reduces a time series to a target number of points while preserving
visual shape — peaks and troughs are retained.
"""

from __future__ import annotations

import statistics


def running_median(
    data: list[tuple[float, float]],
    window_seconds: float,
) -> list[tuple[float, float]]:
    """Smooth a time series with a running median filter.

    Args:
        data: List of (timestamp, value) tuples, sorted by timestamp.
        window_seconds: Width of the sliding window in seconds.

    Returns:
        List of (timestamp, median_value) for each original timestamp.
    """
    if len(data) <= 1:
        return list(data)

    half = window_seconds / 2
    result: list[tuple[float, float]] = []
    n = len(data)

    for i in range(n):
        t = data[i][0]
        lo = t - half
        hi = t + half
        # Collect values within the window — scan outward from i
        vals: list[float] = [data[i][1]]
        j = i - 1
        while j >= 0 and data[j][0] >= lo:
            vals.append(data[j][1])
            j -= 1
        j = i + 1
        while j < n and data[j][0] <= hi:
            vals.append(data[j][1])
            j += 1
        result.append((t, statistics.median(vals)))

    return result


def downsample(data: list[tuple[float, float]], threshold: int) -> list[tuple[float, float]]:
    """Downsample data using LTTB.

    Args:
        data: List of (x, y) tuples, sorted by x.
        threshold: Target number of output points.

    Returns:
        Downsampled list of (x, y) tuples.
    """
    n = len(data)
    if threshold >= n or threshold < 3:
        return list(data)

    sampled: list[tuple[float, float]] = [data[0]]

    bucket_size = (n - 2) / (threshold - 2)

    a_x, a_y = data[0]

    for i in range(threshold - 2):
        # Next bucket boundaries
        avg_start = int((i + 1) * bucket_size) + 1
        avg_end = int((i + 2) * bucket_size) + 1
        if avg_end > n - 1:
            avg_end = n - 1

        # Average of next bucket
        avg_x = 0.0
        avg_y = 0.0
        count = avg_end - avg_start
        if count <= 0:
            count = 1
        for j in range(avg_start, avg_end):
            avg_x += data[j][0]
            avg_y += data[j][1]
        avg_x /= count
        avg_y /= count

        # Current bucket boundaries
        range_start = int(i * bucket_size) + 1
        range_end = int((i + 1) * bucket_size) + 1
        if range_end > n - 1:
            range_end = n - 1

        # Pick point with largest triangle area
        max_area = -1.0
        max_idx = range_start
        for j in range(range_start, range_end):
            area = abs(
                (a_x - avg_x) * (data[j][1] - a_y)
                - (a_x - data[j][0]) * (avg_y - a_y)
            )
            if area > max_area:
                max_area = area
                max_idx = j

        sampled.append(data[max_idx])
        a_x, a_y = data[max_idx]

    sampled.append(data[-1])
    return sampled
