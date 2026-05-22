"""On-disk format for ``reach.geom`` + a parser / validator for it.

The kayak DB stores reach polyline geometry as a single string in
``reach.geom``. The canonical format is::

    "lon1 lat1,lon2 lat2,…"

— comma-separated, lon-first pairs (matching WKT / GeoJSON convention),
one space between lon and lat in each pair, no LINESTRING wrapper, no
trailing comma. Coordinates are decimal-degree floats (typically rounded
to 6 decimals, ~11 cm at the equator — matches the precision NHD HR
flowlines publish).

Why no LINESTRING wrapper: the PHP map rendering path at
``php/includes/gauge_map.php:61-70`` parses the string by splitting on
``,`` and float-casting each side. ``(float)"LINESTRING(-122.021835"``
returns 0 (PHP's float cast stops at the first non-numeric character),
which lands the first vertex at the prime meridian and draws a long
horizontal line across the Atlantic. This bit migration 0039 (Horse
Creek) and was fixed by migration 0041; the format here is the
canonical contract going forward. See [[feedback_reach_geom_no_wkt]].

This module is intentionally dependency-light — no GDAL / osgeo / numpy
— so the validator (``levels check-reaches``) can import it without
pulling in the heavy tracing toolchain.
"""

from __future__ import annotations

from collections.abc import Iterable

_LAT_RANGE = (-90.0, 90.0)
_LON_RANGE = (-180.0, 180.0)


def validate_lat_lon(lat: float, lon: float) -> None:
    """Raise ``ValueError`` if (*lat*, *lon*) isn't a real point on Earth.

    Strict spherical-coordinate bounds, not a region-specific bbox — that
    way the helper stays generic; if you want US-only checks, layer them
    on top in the caller.
    """
    if not _LAT_RANGE[0] <= lat <= _LAT_RANGE[1]:
        raise ValueError(f"latitude {lat!r} out of range {_LAT_RANGE}")
    if not _LON_RANGE[0] <= lon <= _LON_RANGE[1]:
        raise ValueError(f"longitude {lon!r} out of range {_LON_RANGE}")


def format_geom_for_sql(
    coords: Iterable[tuple[float, float]],
    *,
    precision: int = 6,
) -> str:
    """Render trace ``(lat, lon)`` tuples as the canonical reach.geom string.

    Note the input ordering is ``(lat, lon)`` (matches
    :func:`kayak.tracing.trace.trace_reach`'s output, which yields
    latitude-first tuples to match its CSV column order); the output
    is **lon-first** ``"lon lat,lon lat,…"`` per the on-disk contract.

    Each coordinate is validated via :func:`validate_lat_lon`; out-of-
    range values raise ``ValueError`` with the offending index. The
    full list is materialized so a bad point near the end still aborts
    before we emit a partial geom string.
    """
    pairs: list[str] = []
    for i, (lat, lon) in enumerate(coords):
        try:
            validate_lat_lon(lat, lon)
        except ValueError as exc:
            raise ValueError(f"coord {i}: {exc}") from None
        pairs.append(f"{lon:.{precision}f} {lat:.{precision}f}")
    if not pairs:
        raise ValueError("coord list is empty")
    return ",".join(pairs)


def parse_geom_string(s: str) -> list[tuple[float, float]]:
    """Parse a reach.geom string back into a list of ``(lon, lat)`` tuples.

    Mirrors the PHP parser at ``php/includes/gauge_map.php:61-70`` so
    server-side validation matches what the client renders. Tolerant of
    whitespace around pairs (PHP's parser ``trim``s each one). Raises
    ``ValueError`` if any pair has the wrong arity or fails the float
    cast — these are exactly the conditions that would corrupt the
    PHP-rendered polyline.

    Returns ``(lon, lat)`` tuples (matching the on-disk lon-first
    convention) so the caller can compare directly against the
    ``longitude_start`` / ``longitude_end`` columns without
    re-swapping.
    """
    if not s:
        raise ValueError("empty geom string")
    out: list[tuple[float, float]] = []
    for i, raw_pair in enumerate(s.split(",")):
        pair = raw_pair.strip().split()
        if len(pair) != 2:
            raise ValueError(f"pair {i}: expected 'lon lat', got {raw_pair!r}")
        try:
            lon = float(pair[0])
            lat = float(pair[1])
        except ValueError as exc:
            raise ValueError(f"pair {i}: {exc}") from None
        validate_lat_lon(lat, lon)
        out.append((lon, lat))
    return out


def has_wkt_wrapper(s: str) -> bool:
    """``True`` if *s* looks like it carries a WKT/SQL geometry wrapper.

    Catches ``LINESTRING(...)``, ``MULTILINESTRING(...)``,
    ``POINT(...)``, ``POLYGON(...)``, and a bare leading ``(`` — any of
    which would break the PHP parser the same way the Horse Creek bug
    did. Migration authors should run this before inserting; the
    ``levels check-reaches`` CLI scans existing rows for the same
    pattern.
    """
    upper = s.lstrip().upper()
    for prefix in (
        "LINESTRING",
        "MULTILINESTRING",
        "POINT",
        "POLYGON",
        "MULTIPOINT",
        "GEOMETRYCOLLECTION",
    ):
        if upper.startswith(prefix):
            return True
    return upper.startswith("(")
