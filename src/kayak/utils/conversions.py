"""Unit conversions and date parsing helpers."""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# Common timezone abbreviations to UTC offsets (hours)
TIMEZONE_OFFSETS: dict[str, int] = {
    "EST": -5,
    "EDT": -4,
    "CST": -6,
    "CDT": -5,
    "MST": -7,
    "MDT": -6,
    "PST": -8,
    "PDT": -7,
    "AKST": -9,
    "AKDT": -8,
    "HST": -10,
    "UTC": 0,
    "GMT": 0,
    "Z": 0,
    # USBR_Special zone codes
    "P": 0,  # UTC (Pacific in USBR context but data is UTC)
    "M": -7,  # MST
    "C": -6,  # CST
    "E": -5,  # EST
}


def celsius_to_fahrenheit(c: float) -> float:
    """Convert Celsius to Fahrenheit (mirrors C++ 1.8*c + 32)."""
    return round(c * 1.8 + 32, 1)


def kcfs_to_cfs(kcfs: float) -> float:
    """Convert thousands of CFS to CFS."""
    return kcfs * 1000.0


def cm_to_feet(cm: float) -> float:
    """Convert centimeters to feet (for wave height)."""
    return round(cm * 100 / 2.54 / 12, 1)


def interpolate_rating(
    table: list[tuple[float, float]],
    value: float,
    rounding: float = 0.0,
) -> float | None:
    """Linear interpolation through a rating table.

    table: sorted list of (x, y) pairs
    value: x value to interpolate
    rounding: round result to nearest multiple (0 = no rounding)
    """
    if not table:
        return None

    if value <= table[0][0]:
        return table[0][1]
    if value >= table[-1][0]:
        return table[-1][1]

    for i in range(len(table) - 1):
        x1, y1 = table[i]
        x2, y2 = table[i + 1]
        if x1 <= value <= x2:
            if x2 == x1:
                result = y1
            else:
                result = y1 + (y2 - y1) / (x2 - x1) * (value - x1)
            if rounding > 0:
                result = round(result / rounding) * rounding
            return result

    return None


def parse_datetime(text: str, tz_name: str | None = None) -> datetime | None:
    """Parse a date/time string with optional timezone.

    Tries several common formats used by government agencies.
    Returns a timezone-aware datetime in UTC, or None on failure.
    """
    text = text.strip()
    if not text:
        return None

    # Try ISO-like formats first
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
        "%Y%m%d %H:%M",
        "%Y%m%d%H%M%S",
        "%d-%b-%Y %H:%M",
        "%b %d %Y %H:%M",
    ]

    dt = None
    for fmt in formats:
        try:
            dt = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue

    if dt is None:
        return None

    # Apply timezone offset
    if tz_name:
        tz_name = tz_name.strip().upper()
        offset_hours = TIMEZONE_OFFSETS.get(tz_name)
        if offset_hours is not None:
            from datetime import timedelta

            # Convert from local time to UTC
            dt = dt.replace(tzinfo=UTC) - timedelta(hours=offset_hours)
        else:
            logger.warning("Unknown timezone '%s', falling back to UTC", tz_name)
            dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.replace(tzinfo=UTC)

    return dt


def safe_float(text: str) -> float | None:
    """Parse a string to float, returning None on failure.

    Mirrors Parse::toDouble() — returns None (instead of INFINITY) on error.
    """
    try:
        val = float(text.strip().replace(",", ""))
        if math.isinf(val) or math.isnan(val):
            return None
        return val
    except (ValueError, AttributeError):
        return None


def safe_int(text: str) -> int | None:
    """Parse a string to int, returning None on failure."""
    try:
        return int(text.strip())
    except (ValueError, AttributeError):
        return None
