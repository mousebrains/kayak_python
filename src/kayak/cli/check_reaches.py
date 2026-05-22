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
* **Elevation gap** — reach has full endpoints + a non-zero ``length``
  but ``elevation`` / ``elevation_lost`` / ``gradient`` columns are
  NULL. This was the original Horse Creek miss in migration 0039 — the
  reach was inserted with NULL elevation columns and stayed that way
  until ``scripts/refresh_reach_elevations.py`` was run manually. The
  check fires regardless of whether ``geom`` is present.

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

# ~0.003° lat ≈ 333 m on the ground; matches the worst-case NHD HR
# snap distance we've observed in practice (Horse Creek endpoint
# alignment was ~21 m, well inside this). A drift larger than this
# almost certainly means a manually-typed endpoint column doesn't
# match the trace — either the column is wrong (the Horse Creek,
# Klickitat, South Santiam, SF Owyhee, and Rogue cases caught
# during the initial validator rollout, all fixed by 0041-0044) or
# the trace overshot. Override with --endpoint-tolerance for a
# wider scan if you're inspecting reaches whose take-out you know
# is intentionally off-network.
_ENDPOINT_TOL_DEG = 0.003


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

    # Elevation completeness — fires independent of geom. A reach with full
    # endpoint coords and a non-zero length should have its elevation
    # triple populated; if not, ``scripts/refresh_reach_elevations.py``
    # never ran for it.
    needs_elevation = (
        reach.latitude_start is not None
        and reach.longitude_start is not None
        and reach.latitude_end is not None
        and reach.longitude_end is not None
        and reach.length is not None
        and reach.length > 0
    )
    if needs_elevation:
        missing = [
            col
            for col, val in (
                ("elevation", reach.elevation),
                ("elevation_lost", reach.elevation_lost),
                ("gradient", reach.gradient),
            )
            if val is None
        ]
        if missing:
            issues.append(
                f"{', '.join(missing)} NULL despite endpoints + length present "
                f"— run scripts/refresh_reach_elevations.py "
                f"--reach-ids {reach.id} --apply"
            )

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


def scan_for_issues(
    *,
    database_url: str | None = None,
    endpoint_tolerance: float = _ENDPOINT_TOL_DEG,
) -> tuple[int, list[tuple[str, list[str]]]]:
    """Scan every reach.geom row and return ``(total_reaches, flagged)``.

    ``flagged`` is a list of ``(label, issues)`` pairs — label includes
    the reach id + AW id + display name when available, issues is the
    list of human-readable problems found. An empty ``flagged`` means
    the DB is clean.

    Separated from the CLI wrapper so the pipeline orchestrator can
    drive the same scan without going through ``sys.exit`` (which the
    pipeline's per-step ``SystemExit`` handler would otherwise swallow,
    masking validator failures).
    """
    flagged: list[tuple[str, list[str]]] = []
    with get_session(database_url) as session:
        reaches = session.query(Reach).all()
        for r in reaches:
            issues = _check_one(r, endpoint_tol_deg=endpoint_tolerance)
            if not issues:
                continue
            label = f"reach {r.id}"
            if getattr(r, "aw_id", None):
                label += f" (aw_{r.aw_id})"
            if r.display_name:
                label += f" — {r.display_name}"
            flagged.append((label, issues))
    return len(reaches), flagged


def check_reaches(args: argparse.Namespace) -> None:
    """Entry point for ``levels check-reaches``."""
    total, flagged = scan_for_issues(
        database_url=args.database_url,
        endpoint_tolerance=args.endpoint_tolerance,
    )
    for label, issues in flagged:
        print(label)
        for issue in issues:
            print(f"  • {issue}")
    print()
    print(f"checked {total} reaches; {len(flagged)} with issues")
    sys.exit(0 if not flagged else 1)
