#!/usr/bin/env bash
# Download NHD (National Hydrography Dataset) state GeoPackage files and
# NHDPlus HR HUC4 files named by a dataset-supplied download list.
#
# The TOOL is generic engine tooling — part of the Trace-cache toolchain with
# scripts/extract_*.sh, whose defaults read this checkout's Trace-cache/NHD.
# WHICH states/HUC4s to download is regional knowledge and lives in the
# dataset repo (S3g / gap G5): e.g. kayak_data ops/nhd_downloads.txt.
#
# Run on a machine with sufficient RAM and disk (files are ~200MB-1GB each).
#
# Usage:
#   bash scripts/fetch_nhd.sh ../kayak_data/ops/nhd_downloads.txt [DEST_DIR]
#
# List format (one entry per line; '#' starts a comment):
#   state NHD_H_Oregon_State_GPKG.zip   # medium-res state GeoPackage zip name
#   huc4 1707                           # NHDPlus HR HUC4 GDB code
#
# Default destination: this checkout's Trace-cache/NHD/.

set -euo pipefail

LIST="${1:?Usage: bash scripts/fetch_nhd.sh <download-list> [DEST_DIR] (the regional list lives in the dataset repo, e.g. ../kayak_data/ops/nhd_downloads.txt)}"
DEST="${2:-$(dirname "$0")/../Trace-cache/NHD}"

if [[ ! -f "$LIST" ]]; then
    echo "Error: download list not found: $LIST"
    exit 1
fi

STATE_FILES=()
HR_HUCS=()
while read -r kind value _; do
    [[ -z "$kind" || "$kind" == \#* ]] && continue
    case "$kind" in
        state) STATE_FILES+=("$value") ;;
        huc4) HR_HUCS+=("$value") ;;
        *)
            echo "Error: unknown entry kind '$kind' in $LIST (expected 'state' or 'huc4')"
            exit 1
            ;;
    esac
done <"$LIST"

if [[ ${#STATE_FILES[@]} -eq 0 && ${#HR_HUCS[@]} -eq 0 ]]; then
    echo "Error: $LIST declares no 'state' or 'huc4' entries"
    exit 1
fi

mkdir -p "$DEST/state" "$DEST/hr"

S3="https://prd-tnm.s3.amazonaws.com/StagedProducts"

# --- Medium resolution: full state GeoPackages ---
echo "=== NHD Medium Resolution (State GeoPackages) ==="
# ${arr[@]+...} guards the empty-array case under `set -u` on bash 3.2
# (the dev Mac's /bin/bash), where "${arr[@]}" alone is "unbound variable".
for f in ${STATE_FILES[@]+"${STATE_FILES[@]}"}; do
    dest="$DEST/state/$f"
    if [[ -f "$dest" ]]; then
        echo "  Already have $f, skipping"
        continue
    fi
    echo "  Downloading $f ..."
    curl -# -o "$dest" "$S3/Hydrography/NHD/State/GPKG/$f"
done

# --- High resolution: NHDPlus HR by HUC4 ---
echo ""
echo "=== NHDPlus HR (HUC4 GeoDatabase) ==="
for huc in ${HR_HUCS[@]+"${HR_HUCS[@]}"}; do
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
