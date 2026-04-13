#!/usr/bin/env bash
# Extract the two layers needed for reach tracing from NHDPlus HR HUC4 GDB ZIPs.
#
# For each NHDPLUS_H_{HUC4}_HU4_GDB.zip, produces a trace_{HUC4}.gpkg containing:
#   - flowline: NHDFlowline geometry with NHDPlusID and GNIS_Name
#   - vaa:      NHDPlusFlowlineVAA with NHDPlusID, HydroSeq, DnHydroSeq
#
# This reduces 7.7 GB of raw GDBs to ~5 GB of indexed GPKGs and makes spatial
# queries 700x faster (63s → <0.1s) due to GeoPackage spatial indexing.
#
# Extractions run in parallel (default: 4 at a time).
#
# Usage:
#   bash scripts/extract_trace_data.sh [HR_DIR] [OUTPUT_DIR] [JOBS]
#
# Defaults:
#   HR_DIR     = Trace-cache/NHD/hr
#   OUTPUT_DIR = Trace-cache/trace
#   JOBS       = 4

set -euo pipefail

HR_DIR="${1:-$(dirname "$0")/../Trace-cache/NHD/hr}"
OUTPUT_DIR="${2:-$(dirname "$0")/../Trace-cache/trace}"
MAX_JOBS="${3:-4}"

if [[ ! -d "$HR_DIR" ]]; then
    echo "Error: $HR_DIR not found."
    exit 1
fi

mkdir -p "$OUTPUT_DIR"

# FIFO-based job pool (works on bash 3.2/macOS)
FIFO=$(mktemp -u)
mkfifo "$FIFO"
exec 3<>"$FIFO"
rm "$FIFO"

# Prime the pool with MAX_JOBS tokens
for ((i = 0; i < MAX_JOBS; i++)); do
    echo >&3
done

extract_one() {
    local zip="$1"
    local huc="$2"
    local out="$3"
    local tmp="${out}.tmp"

    rm -f "$tmp" "${tmp}-journal"

    ogr2ogr -f GPKG "$tmp" \
        "/vsizip/$zip" \
        NHDFlowline \
        -select NHDPlusID,GNIS_Name \
        -nln flowline \
        2>/dev/null

    ogr2ogr -f GPKG -append "$tmp" \
        "/vsizip/$zip" \
        -sql "SELECT NHDPlusID, HydroSeq, DnHydroSeq FROM NHDPlusFlowlineVAA" \
        -nln vaa \
        2>/dev/null

    mv "$tmp" "$out"
    local size_mb=$(( $(stat -f%z "$out" 2>/dev/null || stat -c%s "$out" 2>/dev/null || echo 0) / 1024 / 1024 ))
    echo "  HUC4 $huc: done (${size_mb} MB)"

    # Return token to the pool
    echo >&3
}

echo "Extracting trace data to $OUTPUT_DIR (max $MAX_JOBS parallel)..."

for zip in "$HR_DIR"/NHDPLUS_H_*_HU4_GDB.zip; do
    [[ -f "$zip" ]] || continue
    huc=$(basename "$zip" | sed 's/NHDPLUS_H_\([0-9]*\)_.*/\1/')
    out="$OUTPUT_DIR/trace_${huc}.gpkg"

    if [[ -f "$out" ]]; then
        echo "  HUC4 $huc: skipped (already exists)"
        continue
    fi

    # Wait for a token (blocks until a slot is free)
    read -u 3

    echo "  HUC4 $huc: starting..."
    extract_one "$zip" "$huc" "$out" &
done

# Wait for all remaining jobs
wait

exec 3>&-

echo ""
echo "=== Done ==="
total_size=0
count=0
for f in "$OUTPUT_DIR"/trace_*.gpkg; do
    [[ -f "$f" ]] || continue
    total_size=$((total_size + $(stat -f%z "$f" 2>/dev/null || stat -c%s "$f" 2>/dev/null || echo 0)))
    count=$((count + 1))
done
total_mb=$((total_size / 1024 / 1024))
echo "Extracted $count HUC4s to $OUTPUT_DIR (${total_mb} MB total)"
ls -lh "$OUTPUT_DIR"/trace_*.gpkg 2>/dev/null
