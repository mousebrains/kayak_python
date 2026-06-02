"""``levels trace`` — trace a stream reach along NHD HR flowlines.

Thin CLI wrapper around :mod:`kayak.tracing.trace`. The heavy GDAL import
is deferred into the entry function so ``levels --help`` loads fast on
systems that don't have the osgeo package installed — only users who
actually invoke ``levels trace`` pay for it.
"""

from __future__ import annotations

import argparse
import sys


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the 'trace' subcommand."""
    parser = subparsers.add_parser(
        "trace",
        help="Trace a stream reach between put-in and take-out using NHD HR data",
    )
    parser.set_defaults(func=trace)
    parser.add_argument("--putin", required=True, help="Put-in coordinates as LAT,LON")
    parser.add_argument("--takeout", required=True, help="Take-out coordinates as LAT,LON")
    parser.add_argument(
        "--name", default=None, help="Reach name (for map title; default: auto-detect)"
    )
    parser.add_argument(
        "--huc4", default=None, help="HUC4 code (default: auto-detect from coordinates)"
    )
    parser.add_argument(
        "--output", default=None, help="Output base name (default: derived from --name or 'trace')"
    )
    parser.add_argument(
        "--csv-only", action="store_true", help="Output CSV only, skip map generation"
    )
    parser.add_argument(
        "--osm",
        action="store_true",
        help="Prefer the OSM main-channel trace (better at braids/islands), falling "
        "back to the NHD trace via a gate. See kayak.tracing.osm.",
    )
    parser.add_argument(
        "--osm-source",
        default="Trace-cache/OSM/named_waterways.gpkg",
        help="OSM waterway GPKG (built by scripts/extract_osm_waterways.sh)",
    )


def trace(args: argparse.Namespace) -> None:
    """Entry point for ``levels trace``.

    Imports ``kayak.tracing.trace`` lazily so the GDAL/osgeo dependency
    is only loaded when this command actually runs.
    """
    try:
        from kayak.tracing import trace as impl
    except ImportError as exc:
        print(
            f"error: cannot load tracing module — GDAL/osgeo may be missing: {exc}",
            file=sys.stderr,
        )
        sys.exit(2)

    _pi = [float(x) for x in args.putin.split(",")]
    _to = [float(x) for x in args.takeout.split(",")]
    putin: tuple[float, float] = (_pi[0], _pi[1])
    takeout: tuple[float, float] = (_to[0], _to[1])

    if args.output:
        base = args.output
    elif args.name:
        base = args.name.lower().replace(" ", "_") + "_trace"
    else:
        base = "trace"

    if args.osm:
        from kayak.tracing import osm as osm_impl

        coords, source = osm_impl.trace_reach(
            putin,
            takeout,
            river=args.name,
            osm_source=args.osm_source,
            huc4=args.huc4,
            verbose=True,
        )
        if not coords:
            print("error: no trace produced (OSM and NHD both unavailable)", file=sys.stderr)
            sys.exit(2)
        print(f"Geometry source: {source}")
    else:
        coords = impl.trace_reach(putin, takeout, huc4=args.huc4, verbose=True)
    miles = impl.total_distance(coords)

    csv_file = f"{base}.csv"
    impl.write_csv(coords, csv_file)
    print(f"Wrote {csv_file}")

    # SQL-ready geom string for the migration author to paste into the
    # reach row's `geom` column. Canonical format per kayak.tracing.format —
    # no LINESTRING wrapper, lon-first pairs, comma-separated.
    geom_file = f"{base}.geom.sql.txt"
    impl.write_geom_sql(coords, geom_file)
    print(f"Wrote {geom_file}")

    if not args.csv_only:
        png_file = f"{base}.png"
        impl.make_map(coords, putin, takeout, args.name, miles, png_file)
        print(f"Wrote {png_file}")

    print(f"\nPut-in:   {putin[0]:.6f}, {putin[1]:.6f}")
    print(f"Take-out: {takeout[0]:.6f}, {takeout[1]:.6f}")
    print(f"Distance: {miles:.1f} miles")
