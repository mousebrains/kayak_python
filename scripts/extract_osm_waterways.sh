#!/usr/bin/env bash
# Extract named waterways from OSM PBF files into a single GeoPackage.
#
# Parallel to extract_nhd_flowlines.sh but for OpenStreetMap data.
# Requires: osmium (brew install osmium-tool), ogr2ogr (brew install gdal).
#
# Usage:
#   bash scripts/extract_osm_waterways.sh [OSM_DIR] [OUTPUT]
#
# Defaults:
#   OSM_DIR = ./Trace-cache/OSM
#   OUTPUT  = ./Trace-cache/OSM/named_waterways.gpkg

set -euo pipefail

OSM_DIR="${1:-$(dirname "$0")/../Trace-cache/OSM}"
OUTPUT="${2:-$OSM_DIR/named_waterways.gpkg}"
TMPDIR="${TMPDIR:-/tmp}"

if [[ ! -d "$OSM_DIR" ]]; then
    echo "Error: $OSM_DIR not found."
    exit 1
fi

# Waterway types relevant for paddling
WATERWAY_TYPES="river,stream,canal"

# Spatial filter for California: lat >= 40° (same as NHD extraction)
CA_SPAT_ARGS=(-spat -180 40.0 180 90)

# PBFs to skip (e.g. nodes-only extracts that have no waterway linestrings)
SKIP_PBFS="norcal"

rm -f "$OUTPUT"

count=0
for pbf in "$OSM_DIR"/*-latest.osm.pbf; do
    [[ -f "$pbf" ]] || continue
    state=$(basename "$pbf" | sed 's/-latest\.osm\.pbf//')

    if echo "$SKIP_PBFS" | grep -qw "$state"; then
        echo "Skipping $state (in SKIP_PBFS list)"
        continue
    fi

    echo "Processing $state ..."

    # Step 1: Filter to waterway ways only (much faster than letting ogr2ogr scan the full PBF)
    filtered="$TMPDIR/osm_waterways_${state}.osm.pbf"
    osmium tags-filter "$pbf" w/waterway=$WATERWAY_TYPES --overwrite -o "$filtered"

    # Step 2: Convert to GeoPackage, keeping only named features
    spat_args=()
    if [[ "$state" == "california" ]]; then
        spat_args=("${CA_SPAT_ARGS[@]}")
        echo "  (filtering to lat >= 40°)"
    fi

    append_args=()
    if [[ $count -gt 0 ]]; then
        append_args=(-update -append)
    fi

    # Use -sql instead of -where/-select to work with -append
    sql="SELECT osm_id, name, waterway, other_tags FROM lines WHERE name IS NOT NULL AND waterway IN ('river','stream','canal')"

    ogr2ogr -f GPKG "$OUTPUT" "$filtered" \
        -sql "$sql" \
        ${spat_args[@]+"${spat_args[@]}"} \
        -nln waterway ${append_args[@]+"${append_args[@]}"} \
        || echo "  Warning: extraction failed for $state"

    rm -f "$filtered"

    new_count=$(ogrinfo -sql "SELECT COUNT(*) FROM waterway" "$OUTPUT" 2>/dev/null | grep -oE '[0-9]+' | tail -1 || echo "?")
    echo "  Total waterways so far: $new_count"
    count=$((count + 1))
done

if [[ $count -eq 0 ]]; then
    echo "No PBF files found in $OSM_DIR"
    exit 1
fi

# Build spatial index for faster queries
echo ""
echo "Building spatial index..."
ogrinfo "$OUTPUT" -sql "SELECT CreateSpatialIndex('waterway', 'geom')" >/dev/null 2>&1 || true

echo ""
echo "=== Done ==="
echo "Output: $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"
ogrinfo -sql "SELECT COUNT(*) FROM waterway" "$OUTPUT" 2>/dev/null || true
echo ""
echo "Sample names:"
ogrinfo -sql "SELECT DISTINCT name FROM waterway ORDER BY name LIMIT 20" "$OUTPUT" 2>/dev/null || true
