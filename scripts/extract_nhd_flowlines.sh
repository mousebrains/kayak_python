#!/usr/bin/env bash
# Extract named flowlines from NHDPlus HR HUC4 GDB files into a single GeoPackage.
#
# Run after fetch_nhd.sh on a machine with ogr2ogr (brew install gdal).
#
# Usage:
#   bash scripts/extract_nhd_flowlines.sh [NHD_DIR] [OUTPUT]
#
# Defaults:
#   NHD_DIR = ./NHD-cache
#   OUTPUT  = ./NHD-cache/named_flowlines.gpkg

set -euo pipefail

NHD_DIR="${1:-$(dirname "$0")/../NHD-cache}"
OUTPUT="${2:-$NHD_DIR/named_flowlines.gpkg}"
HR_DIR="$NHD_DIR/hr"

if [[ ! -d "$HR_DIR" ]]; then
    echo "Error: $HR_DIR not found. Run fetch_nhd.sh first."
    exit 1
fi

# Remove existing output to start fresh
rm -f "$OUTPUT"

# HUC4 regions that need latitude filtering (Nevada/California)
# These extend south of 40° — clip to north only
FILTER_HUCS="1601 1602 1603 1604 1803"

count=0
for zip in "$HR_DIR"/NHDPLUS_H_*_HU4_GDB.zip; do
    [[ -f "$zip" ]] || continue
    huc=$(basename "$zip" | sed 's/NHDPLUS_H_\([0-9]*\)_.*/\1/')
    echo "Processing HUC4 $huc ..."

    # Build the WHERE clause
    where="gnis_name IS NOT NULL"
    if echo "$FILTER_HUCS" | grep -qw "$huc"; then
        where="$where AND OGR_GEOM_MAXY >= 40.0"
        echo "  (filtering to lat >= 40°)"
    fi

    # The GDB inside the zip has a path like NHDPLUS_H_1707_HU4_GDB.gdb
    gdb_name="NHDPLUS_H_${huc}_HU4_GDB.gdb"

    if [[ $count -eq 0 ]]; then
        ogr2ogr -f GPKG "$OUTPUT" \
            "/vsizip/$zip/$gdb_name" NHDFlowline \
            -where "$where" \
            -nln flowline \
            -select "permanent_identifier,gnis_name,gnis_id,ftype,fcode,lengthkm,reachcode" \
            2>&1 | tail -1 || echo "  Warning: extraction may have partially failed"
    else
        ogr2ogr -f GPKG "$OUTPUT" \
            "/vsizip/$zip/$gdb_name" NHDFlowline \
            -where "$where" \
            -nln flowline -append \
            -select "permanent_identifier,gnis_name,gnis_id,ftype,fcode,lengthkm,reachcode" \
            2>&1 | tail -1 || echo "  Warning: extraction may have partially failed"
    fi

    new_count=$(ogrinfo -sql "SELECT COUNT(*) FROM flowline" "$OUTPUT" 2>/dev/null | grep -oP '\d+' | tail -1 || echo "?")
    echo "  Total flowlines so far: $new_count"
    count=$((count + 1))
done

if [[ $count -eq 0 ]]; then
    echo "No HUC4 files found in $HR_DIR"
    exit 1
fi

echo ""
echo "=== Done ==="
echo "Output: $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"
ogrinfo -sql "SELECT COUNT(*) FROM flowline" "$OUTPUT" 2>/dev/null || true
echo ""
echo "Copy to dev server:"
echo "  rsync -av $OUTPUT yourserver:~/kayak/NHD-cache/"
