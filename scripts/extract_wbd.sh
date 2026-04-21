#!/usr/bin/env bash
# Extract WBD watershed-boundary polygon layers from NHDPlus HR HUC4 GDB ZIPs
# into a single GeoPackage with all six WBD levels (HUC2/4/6/8/10/12).
#
# Used as the source of truth by `levels assign-huc` to assign a 12-digit HUC12
# to every reach via point-in-polygon lookup of its put-in coordinates. See
# docs/PLAN_huc_assignment.md for context.
#
# Sequential (not parallel like extract_trace_data.sh) because all 19 HUC4 ZIPs
# append into one output file — concurrent ogr2ogr writers would corrupt the GPKG.
#
# Idempotent: exits 0 if Trace-cache/wbd.gpkg already exists. Delete the file to
# force a re-extract (e.g. after a USGS WBD release refresh).
#
# Usage:
#   bash scripts/extract_wbd.sh [HR_DIR] [OUT]
#
# Defaults:
#   HR_DIR = Trace-cache/NHD/hr
#   OUT    = Trace-cache/wbd.gpkg

set -euo pipefail

HR_DIR="${1:-$(dirname "$0")/../Trace-cache/NHD/hr}"
OUT="${2:-$(dirname "$0")/../Trace-cache/wbd.gpkg}"

if [[ ! -d "$HR_DIR" ]]; then
    echo "Error: $HR_DIR not found — run on the host that holds the HUC4 GDB cache (macOS dev)."
    exit 1
fi

if [[ -f "$OUT" ]]; then
    echo "$OUT already exists — delete it to force re-extract."
    exit 0
fi

shopt -s nullglob
zips=("$HR_DIR"/NHDPLUS_H_*_HU4_GDB.zip)
if [[ ${#zips[@]} -eq 0 ]]; then
    echo "Error: no NHDPLUS_H_*_HU4_GDB.zip files in $HR_DIR."
    exit 1
fi

mkdir -p "$(dirname "$OUT")"

for zip in "${zips[@]}"; do
    huc4=$(basename "$zip" | sed -E 's/NHDPLUS_H_([0-9]+)_HU4_GDB\.zip/\1/')
    gdb="/vsizip/$zip/NHDPLUS_H_${huc4}_HU4_GDB.gdb"
    for layer in WBDHU2 WBDHU4 WBDHU6 WBDHU8 WBDHU10 WBDHU12; do
        echo "  $huc4 / $layer"
        ogr2ogr -f GPKG -update -append -nln "$layer" "$OUT" "$gdb" "$layer"
    done
done

echo
echo "Done. Layer summary:"
ogrinfo -so "$OUT"
