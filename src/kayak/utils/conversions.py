"""Unit conversions and date parsing helpers."""

import logging
import math
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# Timezone abbreviations → UTC offset in hours.
#
# USGS RDB feeds publish ``tz_cd`` as one of these abbreviations. Hawaii and
# American Samoa never observe DST, so HST==HAST and SST has no daylight
# pair. Atlantic (Puerto Rico, USVI), Chamorro (Guam, CNMI) and Wake/Samoa
# are rare but appear on a handful of USGS sites.
#
# Unknown abbreviations are treated as a parse failure by ``parse_datetime``
# (returns ``None``) so the row is dropped — UTC-stamping a naive local
# timestamp would silently shift the observation by hours.
TIMEZONE_OFFSETS: dict[str, int] = {
    # CONUS
    "EST": -5, "EDT": -4,
    "CST": -6, "CDT": -5,
    "MST": -7, "MDT": -6,
    "PST": -8, "PDT": -7,
    # Alaska
    "AKST": -9, "AKDT": -8,
    # Hawaii-Aleutian (Hawaii does not observe DST; Aleutians do)
    "HST": -10, "HAST": -10, "HADT": -9,
    # Atlantic - Puerto Rico, USVI (no DST), and the unused-but-published
    # daylight pair.
    "AST": -4, "ADT": -3,
    # Pacific territories - none of these observe DST
    "SST": -11,                # American Samoa
    "CHST": 10,                # Guam, CNMI - Chamorro Standard
    "WAKT": 12,                # Wake Island
    # UTC / GMT synonyms
    "UTC": 0, "GMT": 0, "Z": 0,
    # USBR_Special zone codes (single-letter)
    "P": 0,    # UTC (Pacific in USBR context but data is UTC)
    "M": -7,   # MST
    "C": -6,   # CST
    "E": -5,   # EST
}  # fmt: skip


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


def parse_datetime(
    text: str,
    tz_name: str | None = None,
    *,
    assume_naive: bool = False,
) -> datetime | None:
    """Parse a date/time string with optional timezone.

    Tries several common formats used by government agencies.

    Return type depends on flags:
      * ``tz_name`` set → tz-aware UTC datetime (input interpreted in tz_name)
      * ``assume_naive=True`` → naive datetime (caller handles TZ later, e.g.
        via ``BaseParser.dump_to_db`` and ``source.timezone``)
      * neither → tz-aware UTC datetime (assumes the input is already UTC)

    Returns ``None`` on parse failure.
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
        if offset_hours is None:
            # Unknown abbreviation — refuse to guess. Stamping UTC on a naive
            # local datetime silently shifts the observation by hours, which
            # is worse than dropping the row.
            logger.error("Unknown timezone abbreviation '%s' — dropping row", tz_name)
            return None
        from datetime import timedelta

        # Convert from local time to UTC
        dt = dt.replace(tzinfo=UTC) - timedelta(hours=offset_hours)
    elif assume_naive:
        # Caller will apply per-station TZ later (see BaseParser.dump_to_db).
        pass
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
