#!/usr/bin/env python3
"""Merge NHD and OSM flowlines into a single high-resolution GeoPackage.

Strategy:
  1. Load both datasets, group features by normalized stream name
  2. For streams in both datasets, compare vertex density (points per km)
  3. Keep the higher-resolution geometry for each spatial segment
  4. Include features unique to either dataset

The output is a unified GeoPackage suitable for matching against reach
put-in/take-out coordinates to provide stream traces.

Requirements: GDAL Python bindings (osgeo), shapely >= 2.0

Usage:
    python3 scripts/merge_flowlines.py [OPTIONS]

    # Preview stats without writing output
    python3 scripts/merge_flowlines.py --dry-run

    # Custom paths
    python3 scripts/merge_flowlines.py \
        --nhd Trace-cache/NHD/named_flowlines.gpkg \
        --osm Trace-cache/OSM/named_waterways.gpkg \
        --output Trace-cache/merged_flowlines.gpkg
"""

import argparse
import re
import sys
import time
from collections import defaultdict
from math import cos, radians, sqrt

from osgeo import ogr, osr
from shapely import get_coordinates, line_merge
from shapely import wkb as shapely_wkb
from shapely.geometry import LineString, MultiLineString

ogr.UseExceptions()


def flatten_to_lines(geoms):
    """Flatten a list of LineString/MultiLineString into a list of LineStrings."""
    out = []
    for g in geoms:
        if isinstance(g, MultiLineString):
            out.extend(g.geoms)
        elif isinstance(g, LineString):
            out.append(g)
    return out


def safe_multi(geoms):
    """Create a MultiLineString from a mixed list, flattening as needed."""
    lines = flatten_to_lines(geoms)
    if len(lines) == 0:
        return LineString()
    if len(lines) == 1:
        return lines[0]
    return MultiLineString(lines)


# --- Helpers ----------------------------------------------------------------

# Words to strip for fuzzy name matching
STRIP_WORDS = {
    "north",
    "south",
    "east",
    "west",
    "fork",
    "branch",
    "prong",
    "creek",
    "river",
    "stream",
    "run",
    "brook",
    "ditch",
    "canal",
    "slough",
    "wash",
    "draw",
    "gulch",
    "little",
    "big",
    "upper",
    "lower",
    "middle",
}

# Common abbreviations
ABBREVS = {
    "n": "north",
    "s": "south",
    "e": "east",
    "w": "west",
    "fk": "fork",
    "br": "branch",
    "cr": "creek",
    "r": "river",
    "ck": "creek",
    "lk": "lake",
    "mt": "mount",
}


def normalize_name(name):
    """Normalize a stream name for matching.

    Returns (full_normalized, stem) where stem strips directional/type words
    for fuzzy matching across datasets that may use different conventions.
    """
    if not name:
        return "", ""
    s = name.lower().strip()
    s = re.sub(r"[''`]", "", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    words = s.split()
    # Expand abbreviations
    words = [ABBREVS.get(w, w) for w in words]
    full = " ".join(words)
    # Stem: remove generic type/direction words for cross-dataset matching
    stem_words = [w for w in words if w not in STRIP_WORDS]
    stem = " ".join(stem_words) if stem_words else full
    return full, stem


def haversine_length_km(coords):
    """Approximate LineString length in km from lon/lat coordinate pairs."""
    total = 0.0
    for i in range(len(coords) - 1):
        lon1, lat1 = coords[i][:2]
        lon2, lat2 = coords[i + 1][:2]
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = dlat * dlat + cos(radians((lat1 + lat2) / 2)) ** 2 * dlon * dlon
        total += 6371.0 * sqrt(a)
    return total


def vertex_density(geom):
    """Points per km for a Shapely LineString/MultiLineString."""
    coords = get_coordinates(geom)
    n = len(coords)
    if n < 2:
        return 0.0
    km = haversine_length_km(coords)
    if km < 0.001:
        return 0.0
    return n / km


def load_features(gpkg_path, layer_name, name_field, source_tag, bbox=None):
    """Load features from a GeoPackage, return list of (name, stem, geom, attrs) tuples.

    If bbox is (min_lon, min_lat, max_lon, max_lat), applies a spatial filter
    at the GDAL layer level for much faster loading from large files.
    """
    ds = ogr.Open(gpkg_path, 0)
    if ds is None:
        print(f"Error: cannot open {gpkg_path}", file=sys.stderr)
        sys.exit(1)
    layer = ds.GetLayerByName(layer_name)
    if layer is None:
        print(f"Error: layer '{layer_name}' not found in {gpkg_path}", file=sys.stderr)
        sys.exit(1)

    if bbox:
        layer.SetSpatialFilterRect(*bbox)

    features = []
    count = layer.GetFeatureCount()
    print(
        f"  Loading {count} features from {gpkg_path} [{layer_name}]"
        f"{' (bbox filtered)' if bbox else ''}..."
    )

    for feat in layer:
        name = feat.GetField(name_field)
        if not name:
            continue
        geom_ref = feat.GetGeometryRef()
        if geom_ref is None:
            continue
        # Convert OGR geometry to Shapely via WKB
        wkb_data = bytes(geom_ref.ExportToWkb())
        try:
            shp_geom = shapely_wkb.loads(wkb_data)
        except Exception:
            continue
        if shp_geom.is_empty:
            continue

        full_name, stem = normalize_name(name)
        features.append(
            (
                full_name,
                stem,
                shp_geom,
                {
                    "source": source_tag,
                    "original_name": name,
                },
            )
        )

    ds = None  # close
    return features


def group_by_name(features):
    """Group features by their normalized stem name, with spatial clustering."""
    groups = defaultdict(list)
    for full_name, stem, geom, attrs in features:
        groups[stem].append((full_name, geom, attrs))
    return groups


# --- Merge logic ------------------------------------------------------------


def merge_stream_group(nhd_feats, osm_feats):
    """Merge features for a single stream name.

    For each spatial region, pick the higher-resolution source.
    Returns list of (geom, attrs) to include in output.
    """
    results = []

    # If only one source has features, return all of them
    if not nhd_feats and not osm_feats:
        return results
    if not nhd_feats:
        for _name, geom, attrs in osm_feats:
            attrs["merge_reason"] = "osm_only"
            results.append((geom, attrs))
        return results
    if not osm_feats:
        for _name, geom, attrs in nhd_feats:
            attrs["merge_reason"] = "nhd_only"
            results.append((geom, attrs))
        return results

    # Both sources have features — compare spatially
    nhd_geoms = [g for _, g, _ in nhd_feats]
    osm_geoms = [g for _, g, _ in osm_feats]

    # Try to merge connected segments (NHD is often split at confluences)
    nhd_merged = line_merge(safe_multi(nhd_geoms))
    osm_merged = line_merge(safe_multi(osm_geoms))

    nhd_density = vertex_density(nhd_merged)
    osm_density = vertex_density(osm_merged)
    nhd_len = haversine_length_km(get_coordinates(nhd_merged))
    osm_len = haversine_length_km(get_coordinates(osm_merged))

    # Decision: use the source with better vertex density, but only if it
    # covers a reasonable fraction of the other source's length
    coverage_ratio = (
        min(nhd_len, osm_len) / max(nhd_len, osm_len) if max(nhd_len, osm_len) > 0 else 0
    )

    if coverage_ratio > 0.3:
        # Both cover similar extent — pick higher density
        if osm_density > nhd_density * 1.2:
            # OSM is meaningfully denser — use it
            for _name, geom, attrs in osm_feats:
                attrs["merge_reason"] = "osm_higher_density"
                attrs["density"] = f"{osm_density:.1f}"
                results.append((geom, attrs))
        else:
            # NHD wins (or roughly equal — prefer authoritative source)
            for _name, geom, attrs in nhd_feats:
                attrs["merge_reason"] = "nhd_preferred"
                attrs["density"] = f"{nhd_density:.1f}"
                results.append((geom, attrs))
    else:
        # Coverage differs significantly — include both (they likely cover
        # different parts of the stream, e.g., NHD has tributaries OSM doesn't)
        for _name, geom, attrs in nhd_feats:
            attrs["merge_reason"] = "nhd_extended_coverage"
            results.append((geom, attrs))
        for _name, geom, attrs in osm_feats:
            attrs["merge_reason"] = "osm_extended_coverage"
            results.append((geom, attrs))

    return results


# --- Spatial matching -------------------------------------------------------


def match_groups(nhd_groups, osm_groups):
    """Match NHD and OSM groups by stem name.

    Returns list of (stem, nhd_feats, osm_feats) where either side can be empty.
    """
    all_stems = set(nhd_groups.keys()) | set(osm_groups.keys())
    matched = []
    for stem in sorted(all_stems):
        nhd = nhd_groups.get(stem, [])
        osm = osm_groups.get(stem, [])
        matched.append((stem, nhd, osm))
    return matched


# --- Output -----------------------------------------------------------------


def write_output(results, output_path):
    """Write merged features to a GeoPackage."""
    drv = ogr.GetDriverByName("GPKG")
    ds = drv.CreateDataSource(output_path)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    # GDAL expects lat/lon axis order for EPSG:4326, but our data is lon/lat
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    layer = ds.CreateLayer("flowline", srs, ogr.wkbLineString)
    layer.CreateField(ogr.FieldDefn("name", ogr.OFTString))
    layer.CreateField(ogr.FieldDefn("source", ogr.OFTString))
    layer.CreateField(ogr.FieldDefn("merge_reason", ogr.OFTString))
    layer.CreateField(ogr.FieldDefn("density", ogr.OFTString))

    feat_defn = layer.GetLayerDefn()
    written = 0

    for geom, attrs in results:
        # Handle MultiLineString by writing each component as a separate feature
        if isinstance(geom, MultiLineString):
            parts = list(geom.geoms)
        elif isinstance(geom, LineString):
            parts = [geom]
        else:
            continue

        for part in parts:
            if part.is_empty or len(part.coords) < 2:
                continue
            feat = ogr.Feature(feat_defn)
            feat.SetField("name", attrs.get("original_name", ""))
            feat.SetField("source", attrs.get("source", ""))
            feat.SetField("merge_reason", attrs.get("merge_reason", ""))
            feat.SetField("density", attrs.get("density", ""))
            ogr_geom = ogr.CreateGeometryFromWkb(part.wkb)
            feat.SetGeometry(ogr_geom)
            layer.CreateFeature(feat)
            written += 1

    ds = None  # flush and close
    return written


# --- Main -------------------------------------------------------------------


def main():
    base = "."
    parser = argparse.ArgumentParser(
        description="Merge NHD and OSM flowlines into a unified GeoPackage"
    )
    parser.add_argument(
        "--nhd",
        default=f"{base}/Trace-cache/NHD/named_flowlines.gpkg",
        help="NHD named flowlines GeoPackage",
    )
    parser.add_argument(
        "--osm",
        default=f"{base}/Trace-cache/OSM/named_waterways.gpkg",
        help="OSM named waterways GeoPackage",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=f"{base}/Trace-cache/merged_flowlines.gpkg",
        help="Output merged GeoPackage",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print stats without writing output")
    parser.add_argument("--bbox", help="Spatial filter: min_lon,min_lat,max_lon,max_lat")
    args = parser.parse_args()

    t0 = time.time()

    bbox = None
    if args.bbox:
        bbox = tuple(float(x) for x in args.bbox.split(","))

    print("=== Loading NHD flowlines ===")
    nhd_features = load_features(args.nhd, "flowline", "gnis_name", "nhd", bbox=bbox)
    print(f"  {len(nhd_features)} named NHD features")

    print("\n=== Loading OSM waterways ===")
    osm_features = load_features(args.osm, "waterway", "name", "osm", bbox=bbox)
    print(f"  {len(osm_features)} named OSM features")

    print("\n=== Grouping by stream name ===")
    nhd_groups = group_by_name(nhd_features)
    osm_groups = group_by_name(osm_features)

    nhd_only = set(nhd_groups.keys()) - set(osm_groups.keys())
    osm_only = set(osm_groups.keys()) - set(nhd_groups.keys())
    both = set(nhd_groups.keys()) & set(osm_groups.keys())

    print(f"  NHD-only streams: {len(nhd_only)}")
    print(f"  OSM-only streams: {len(osm_only)}")
    print(f"  Both datasets:    {len(both)}")

    if args.dry_run:
        # Show some comparison stats for streams in both
        print("\n=== Sample density comparisons (streams in both) ===")
        comparisons = []
        for stem in sorted(both)[:200]:
            nhd_feats = nhd_groups[stem]
            osm_feats = osm_groups[stem]
            nhd_geoms = [g for _, g, _ in nhd_feats]
            osm_geoms = [g for _, g, _ in osm_feats]
            nhd_m = line_merge(safe_multi(nhd_geoms))
            osm_m = line_merge(safe_multi(osm_geoms))
            nhd_d = vertex_density(nhd_m)
            osm_d = vertex_density(osm_m)
            nhd_l = haversine_length_km(get_coordinates(nhd_m))
            osm_l = haversine_length_km(get_coordinates(osm_m))
            winner = "OSM" if osm_d > nhd_d * 1.2 else "NHD"
            comparisons.append((stem, nhd_d, osm_d, nhd_l, osm_l, winner))

        print(
            f"  {'Stream':<30} {'NHD pt/km':>10} {'OSM pt/km':>10} {'NHD km':>8} {'OSM km':>8} {'Winner':>6}"
        )
        print(f"  {'-' * 30} {'-' * 10} {'-' * 10} {'-' * 8} {'-' * 8} {'-' * 6}")
        for stem, nd, od, nl, ol, w in sorted(comparisons, key=lambda x: x[2] - x[1], reverse=True)[
            :30
        ]:
            print(f"  {stem:<30} {nd:>10.1f} {od:>10.1f} {nl:>8.1f} {ol:>8.1f} {w:>6}")

        osm_wins = sum(1 for _, _, _, _, _, w in comparisons if w == "OSM")
        print(
            f"\n  Of {len(comparisons)} compared: OSM wins {osm_wins}, NHD wins {len(comparisons) - osm_wins}"
        )
        print(f"\n  Elapsed: {time.time() - t0:.1f}s")
        return

    print("\n=== Merging ===")
    matched = match_groups(nhd_groups, osm_groups)
    all_results = []
    stats = defaultdict(int)

    for _stem, nhd_feats, osm_feats in matched:
        merged = merge_stream_group(nhd_feats, osm_feats)
        for _geom, attrs in merged:
            stats[attrs["merge_reason"]] += 1
        all_results.extend(merged)

    print(f"  Total output features: {len(all_results)}")
    for reason, cnt in sorted(stats.items()):
        print(f"    {reason}: {cnt}")

    print(f"\n=== Writing {args.output} ===")
    written = write_output(all_results, args.output)
    print(f"  Wrote {written} features")

    import os

    size_mb = os.path.getsize(args.output) / 1024 / 1024
    print(f"  File size: {size_mb:.1f} MB")
    print(f"  Elapsed: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
