"""``levels check-reaches`` — validate reach.geom + lat/lon sanity.

Scans every reach row in the DB and reports rows whose geometry would
fail the PHP map parser, whose coordinates are out of valid ranges, or
whose ``latitude_start`` / ``longitude_start`` / ``latitude_end`` /
``longitude_end`` columns don't line up with the first/last vertex of
the polyline.

Catches:

* **WKT wrappers** — ``LINESTRING(…)``, ``MULTILINESTRING(…)``, etc.
  PHP's ``gauge_map.php:61-70`` parser splits on commas and float-casts
  each side; the cast strips non-numeric prefixes silently, landing the
  first vertex at (0°, lat) somewhere in the Atlantic. This is the bug
  Horse Creek hit in migration 0039 (fixed by 0041).
* **Out-of-range coordinates** — anything outside lat ``[-90, 90]`` /
  lon ``[-180, 180]``. Usually indicates a missing minus sign or a
  swapped lat/lon.
* **Empty pairs / wrong arity** — a stray comma or a single value where
  a ``"lon lat"`` pair is expected.
* **Endpoint drift** — first/last geom vertex too far from
  ``latitude_start`` / ``longitude_start`` / ``latitude_end`` /
  ``longitude_end``. NHD trace snapping introduces some slop (we've
  seen ~20 m), but a hard miss usually means the start/end columns
  were copied wrong.

Exit codes:

* ``0`` — no issues
* ``1`` — one or more issues found

Designed to run on the live DB (read-only) and in pre-commit / CI
contexts. The validator imports :mod:`kayak.tracing.format` directly so
it doesn't pull in GDAL / osgeo (the heavy ``kayak.tracing.trace``
import chain), which keeps this command fast to load.
"""

from __future__ import annotations

import argparse
import math
import sys
from decimal import Decimal

from kayak.db.engine import get_session
from kayak.db.models import Reach
from kayak.tracing.format import has_wkt_wrapper, parse_geom_string

# ~0.009° lat ≈ 1 km on the ground. Set wide enough to absorb NHD's
# normal stop-short-of-the-take-out behaviour at tidal mouths and
# reservoir inflows (the Rogue at Foster Bar terminates ~550 m short
# of the tidal take-out, the South Santiam ends at the Foster
# Reservoir entry, etc.) — these are valid traces, just not vertex-
# coincident with the documented landing. Tight enough to still
# catch real bugs like Horse Creek's 12000-km-drift WKT-wrapper bug
# (migration 0041) or the Klickitat's 5-km mis-traced put-in
# (migration 0042). Override with --endpoint-tolerance when you want
# stricter inspection.
_ENDPOINT_TOL_DEG = 0.01


def _addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "check-reaches",
        help="Validate reach.geom format + lat/lon sanity (read-only)",
    )
    parser.set_defaults(func=check_reaches)
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override DATABASE_URL (default: env)",
    )
    parser.add_argument(
        "--endpoint-tolerance",
        type=float,
        default=_ENDPOINT_TOL_DEG,
        help=f"Max allowed degrees between geom endpoint and "
        f"latitude_start/end column (default: {_ENDPOINT_TOL_DEG})",
    )


# main.py calls addArgs (no underscore); export the canonical name.
addArgs = _addArgs


def _endpoint_drift_deg(
    geom_lon: float,
    geom_lat: float,
    col_lat: float | Decimal | None,
    col_lon: float | Decimal | None,
) -> float | None:
    """Return the Euclidean degree distance between two (lat, lon) points.

    Approximation: treats one degree of lat == one degree of lon. Fine
    for the tolerance check (we're flagging *gross* mismatches, not
    measuring distance), and trivially avoids the haversine dependency.

    Accepts ``Decimal`` for the column-side inputs because the Reach
    model uses ``Numeric(9, 6)`` for latitude/longitude columns; we
    cast both sides to float before subtracting.
    """
    if col_lat is None or col_lon is None:
        return None
    return math.hypot(geom_lon - float(col_lon), geom_lat - float(col_lat))


def _check_one(reach: Reach, *, endpoint_tol_deg: float) -> list[str]:
    """Return a list of human-readable issues found for *reach*."""
    issues: list[str] = []
    geom = reach.geom or ""
    if not geom:
        return issues  # geom is optional; empty is fine

    if has_wkt_wrapper(geom):
        issues.append(
            "geom carries a WKT-style wrapper (LINESTRING/POINT/POLYGON/…); "
            "PHP parser will treat the first vertex as longitude 0"
        )
        # Don't bail — try the parser too so we surface as much as possible.

    try:
        vertices = parse_geom_string(geom)
    except ValueError as exc:
        issues.append(f"geom unparseable: {exc}")
        return issues

    if len(vertices) < 2:
        issues.append(f"geom has only {len(vertices)} vertex — needs ≥ 2 for a polyline")
        return issues

    first_lon, first_lat = vertices[0]
    last_lon, last_lat = vertices[-1]

    start_drift = _endpoint_drift_deg(
        first_lon, first_lat, reach.latitude_start, reach.longitude_start
    )
    if start_drift is not None and start_drift > endpoint_tol_deg:
        issues.append(
            f"first geom vertex ({first_lat:.6f}, {first_lon:.6f}) drifts "
            f"{start_drift:.4f}° (~{start_drift * 111:.0f} km) from "
            f"latitude_start/longitude_start columns "
            f"({reach.latitude_start}, {reach.longitude_start})"
        )

    end_drift = _endpoint_drift_deg(last_lon, last_lat, reach.latitude_end, reach.longitude_end)
    if end_drift is not None and end_drift > endpoint_tol_deg:
        issues.append(
            f"last geom vertex ({last_lat:.6f}, {last_lon:.6f}) drifts "
            f"{end_drift:.4f}° (~{end_drift * 111:.0f} km) from "
            f"latitude_end/longitude_end columns "
            f"({reach.latitude_end}, {reach.longitude_end})"
        )

    return issues


def check_reaches(args: argparse.Namespace) -> None:
    """Entry point for ``levels check-reaches``."""
    with get_session(args.database_url) as session:
        reaches = session.query(Reach).all()
        total = len(reaches)
        flagged = 0
        for r in reaches:
            issues = _check_one(r, endpoint_tol_deg=args.endpoint_tolerance)
            if not issues:
                continue
            flagged += 1
            label = f"reach {r.id}"
            if getattr(r, "aw_id", None):
                label += f" (aw_{r.aw_id})"
            if r.display_name:
                label += f" — {r.display_name}"
            print(label)
            for issue in issues:
                print(f"  • {issue}")
        print()
        print(f"checked {total} reaches; {flagged} with issues")
        sys.exit(0 if flagged == 0 else 1)
