#!/usr/bin/env bash
# Download NHD (National Hydrography Dataset) state GeoPackage files
# and NHDPlus HR HUC4 files for OR, WA, ID, NV, and northern CA.
#
# Run on a machine with sufficient RAM and disk (files are ~200MB-1GB each).
#
# Usage:
#   bash scripts/fetch_nhd.sh [DEST_DIR]
#
# Default destination: ./NHD-cache/

set -euo pipefail

DEST="${1:-$(dirname "$0")/../NHD-cache}"
mkdir -p "$DEST/state" "$DEST/hr"

S3="https://prd-tnm.s3.amazonaws.com/StagedProducts"

# --- Medium resolution: full state GeoPackages ---
STATE_FILES=(
    "NHD_H_Oregon_State_GPKG.zip"
    "NHD_H_Washington_State_GPKG.zip"
    "NHD_H_Idaho_State_GPKG.zip"
    "NHD_H_Nevada_State_GPKG.zip"
    "NHD_H_California_State_GPKG.zip"
)

echo "=== NHD Medium Resolution (State GeoPackages) ==="
for f in "${STATE_FILES[@]}"; do
    dest="$DEST/state/$f"
    if [[ -f "$dest" ]]; then
        echo "  Already have $f, skipping"
        continue
    fi
    echo "  Downloading $f ..."
    curl -# -o "$dest" "$S3/Hydrography/NHD/State/GPKG/$f"
done

# --- High resolution: NHDPlus HR by HUC4 ---
# HUC4 regions covering OR, WA, ID, NV, and northern CA (north of 40°):
#   1701 - Pacific Northwest (WA/OR coast)
#   1702 - Willamette
#   1703 - Lower Columbia
#   1704 - Upper Columbia (WA)
#   1705 - Middle Columbia (OR/WA)
#   1706 - Central Oregon
#   1707 - Middle Snake (ID/OR)
#   1708 - Upper Snake (ID)
#   1709 - Closed basins (NV/OR)
#   1710 - Oregon closed basins
#   1711 - Puget Sound (WA)
#   1712 - WA coast
#   1601 - Great Basin (NV)
#   1602 - Great Basin (NV)
#   1603 - Great Basin (NV)
#   1604 - Humboldt (NV)
#   1801 - Klamath-Trinity-Smith (NorCal)
#   1802 - Klamath (OR/CA)
#   1803 - NorCal (north of 40°)
HR_HUCS=(
    1701 1702 1703 1704 1705 1706 1707 1708 1709 1710 1711 1712
    1601 1602 1603 1604
    1801 1802 1803
)

echo ""
echo "=== NHDPlus HR (HUC4 GeoDatabase) ==="
for huc in "${HR_HUCS[@]}"; do
    f="NHDPLUS_H_${huc}_HU4_GDB.zip"
    dest="$DEST/hr/$f"
    if [[ -f "$dest" ]]; then
        echo "  Already have $f, skipping"
        continue
    fi
    url="$S3/Hydrography/NHDPlusHR/Beta/GDB/$f"
    # Check if file exists before downloading
    status=$(curl -sI -o /dev/null -w "%{http_code}" "$url")
    if [[ "$status" == "200" ]]; then
        echo "  Downloading $f ..."
        curl -# -o "$dest" "$url"
    else
        echo "  SKIP $f (HTTP $status)"
    fi
done

echo ""
echo "=== Done ==="
du -sh "$DEST/state" "$DEST/hr" "$DEST"
echo ""
echo "To unzip state GeoPackages:"
echo "  cd $DEST/state && for f in *.zip; do unzip -n \$f; done"
echo ""
echo "NHDPlus HR GDB files can be read directly from zip with GDAL/Fiona/geopandas."
