#!/usr/bin/env bash
# Wheel smoke test (dataset-separation S4a-2 slice C).
#
# Builds the kayak wheel, installs it into a fresh venv OUTSIDE the repo
# checkout, and exercises the packaged engine end-to-end: every runtime
# resource (data YAMLs, the packaged example dataset, schema migrations, web
# static assets, the PHP layer, install templates, LICENSE) must resolve via
# importlib.resources from site-packages — NOT the source tree — and `levels
# init-dataset --example` + `validate-dataset` + `init-db` + `build` must produce
# a deployed site from them.
#
# This is the automated form of the by-hand "install the wheel somewhere else
# and run it" check done during each S4a-2 slice; it turns a future packaging
# regression (a runtime that still reads a repo-root BASE_DIR path, or a data
# file that doesn't ship in the wheel) into a CI failure.
#
# Usage: scripts/wheel-smoke.sh
# Requires: uv (wheel build), python3 (venv). No network beyond pip install.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
# Clean up the scratch dir on exit (success or failure).
trap 'rm -rf "$WORK"' EXIT

DIST="$WORK/dist"
VENV="$WORK/venv"
# An OUTSIDE-the-checkout cwd so a stray `src/` on sys.path can't mask a
# missing wheel resource — imports must come from the installed package.
RUNDIR="$WORK/run"
DOCROOT="$WORK/docroot"
DB="$WORK/kayak.db"
mkdir -p "$RUNDIR" "$DOCROOT"

echo "==> Building wheel from $REPO_ROOT"
uv build --wheel --out-dir "$DIST" "$REPO_ROOT"
WHEEL="$(ls "$DIST"/*.whl)"
echo "    built $WHEEL"

echo "==> Creating fresh venv at $VENV and installing the wheel"
python3 -m venv "$VENV"
# PIP_USER=0 overrides a developer's global `pip --user` default, which a
# venv pip rejects; harmless in CI where no such default exists.
PIP_USER=0 "$VENV/bin/pip" install --quiet --upgrade pip
PIP_USER=0 "$VENV/bin/pip" install --quiet "$WHEEL"

PY="$VENV/bin/python"
LEVELS="$VENV/bin/levels"

echo "==> Resource resolution from site-packages (run from $RUNDIR)"
cd "$RUNDIR"
"$PY" - <<'PYEOF'
from importlib.resources import files
from kayak.resources import resource_dir

root = str(files("kayak"))
assert "site-packages" in root, f"kayak not imported from site-packages: {root}"

# (parts, a file that must ship under it)
# (data/sources.yaml is gone — the S1-cleanup removed the engine seed; the
# source registry is dataset content.)
checks = [
    (("data",), "builder.yaml"),
    (("data", "example_dataset"), "dataset.yaml"),
    (("data", "db", "migrations"), "0001_baseline.sql"),
    (("data", "db", "migrations"), "manifest.csv"),
    (("web", "static"), "map.js"),
    (("web", "static"), "leaflet.js"),
    (("web", "static", "images"), "marker-icon.png"),
    (("web", "php"), "latest.php"),
    (("web", "php", "includes"), "db.php"),
    (("web", "php", "includes"), "states.php"),
    (("web", "php", "_internal"), "index.php"),
    (("web", "install-templates"), "404.html"),
    (("web", "install-templates"), "robots.txt"),
    (("web", "legal"), "LICENSE.txt"),
    (("web", "legal"), "LICENSE-DATA.txt"),
]
for parts, name in checks:
    p = resource_dir(*parts) / name
    assert p.is_file(), f"packaged resource missing from wheel: {'/'.join(parts)}/{name}"
    assert "site-packages" in str(p), f"resolved outside site-packages: {p}"

migs = sorted(resource_dir("data", "db", "migrations").glob("[0-9]*.sql"))
assert len(migs) >= 17, f"expected >=17 packaged migrations, found {len(migs)}"
# S9b: the frozen mixed migrations live in the top-level legacy/ tree (not the
# package), so they must NOT ship in the wheel.
frozen = resource_dir("data", "db", "migrations") / "0003_reach_level_class_checks.sql"
assert not frozen.exists(), f"frozen migration leaked into the wheel: {frozen}"
print(f"    OK — data/web resources resolve from site-packages ({len(migs)} migrations)")
PYEOF

echo "==> CLI entry point"
"$LEVELS" --help >/dev/null
echo "    OK — levels --help"

echo "==> levels fetch-map-layers no-op with empty dataset map config"
EMPTY_MAP_DS="$WORK/empty-map-dataset"
EMPTY_OSMB="$WORK/osmb-empty"
mkdir -p "$EMPTY_MAP_DS"
: > "$EMPTY_MAP_DS/map.yaml"
HOME="$WORK" SUDO_USER="" DATASET_DIR="$EMPTY_MAP_DS" \
    "$LEVELS" fetch-map-layers --output-dir "$EMPTY_OSMB" >/dev/null
if [ -n "$(find "$EMPTY_OSMB" -type f -print -quit)" ]; then
    echo "wheel-smoke FAILED: empty map config wrote overlay files" >&2
    exit 1
fi
echo "    OK — empty map.yaml disables overlay fetches without network"

echo "==> levels init-db (packaged schema + migrations) → $DB"
DATABASE_URL="sqlite:///$DB" "$LEVELS" init-db >/dev/null
test -f "$DB"
echo "    OK — schema created"

echo "==> levels init-dataset --example + validate-dataset (acceptance criterion 1)"
# Materialize the packaged example dataset from the WHEEL (we run from $RUNDIR,
# so --example must resolve it from site-packages, not the source tree) and prove
# it validates. The build below renders from this materialized copy — the new
# command, not a repo-tree path, is what supplies the dataset.
EXAMPLE_DS="$WORK/example-dataset"
HOME="$WORK" SUDO_USER="" "$LEVELS" init-dataset --example "$EXAMPLE_DS" >/dev/null
HOME="$WORK" SUDO_USER="" "$LEVELS" validate-dataset "$EXAMPLE_DS" >/dev/null
test -f "$EXAMPLE_DS/dataset.yaml"
echo "    OK — example dataset materialized from the wheel + validates"

echo "==> levels build (packaged data + web layer) → $DOCROOT"
# DATASET_DIR is the example dataset materialized above (from the wheel) so build
# renders its regression report (fixture_calc_from_usgs.*) from DATASET_DIR/
# regression/ — the S2-E2 path. HOME → $WORK (and SUDO_USER cleared, to disable
# config's ~SUDO_USER/.env fallback) so config doesn't read a developer's
# ~/.config/kayak/.env — which may set its own DATASET_DIR and clash with this
# one. CI's HOME has no such file, so this is a no-op there; it keeps the smoke
# hermetic.
HOME="$WORK" SUDO_USER="" DATABASE_URL="sqlite:///$DB" OUTPUT_DIR="$DOCROOT" \
    SITE_URL="https://levels.example.org" \
    DATASET_DIR="$EXAMPLE_DS" "$LEVELS" build >/dev/null
# The deployed site must carry assets sourced from every packaged tree.
missing=0
for rel in \
    index.html \
    style.css \
    sw.js \
    static/map.js \
    static/leaflet.js \
    static/site-config.json \
    latest.php \
    includes/db.php \
    includes/states.php \
    _internal/index.php \
    404.html \
    robots.txt \
    LICENSE.txt \
    LICENSE-DATA.txt; do
    if [ ! -f "$DOCROOT/$rel" ]; then
        echo "    MISSING from build output: $rel" >&2
        missing=1
    fi
done
[ "$missing" -eq 0 ] || { echo "wheel-smoke FAILED: build output incomplete" >&2; exit 1; }
echo "    OK — build deployed static + php + templates + license from the wheel"

"$PY" - "$DOCROOT/static/site-config.json" <<'PYEOF'
import json
import sys

path = sys.argv[1]
cfg = json.load(open(path, encoding="utf-8"))
assert cfg == {"map": {"center": [0.0, 0.0], "zoom": 2}, "layers": []}, cfg
PYEOF
echo "    OK — generated generic site-config.json from packaged map defaults"

echo "==> regression reports rendered from DATASET_DIR/regression (S2-E2)"
REG="$DOCROOT/static/regression"
for rel in fixture_calc_from_usgs.html fixture_calc_from_usgs.svg fixture_calc_from_usgs.json; do
    [ -f "$REG/$rel" ] || { echo "wheel-smoke FAILED: regression asset missing: $rel" >&2; exit 1; }
done
# The .md was rendered to HTML (sanitized — no raw <script>) and the .svg re-serialized.
if grep -qi "<script" "$REG/fixture_calc_from_usgs.html"; then
    echo "wheel-smoke FAILED: rendered regression HTML contains <script>" >&2; exit 1
fi
grep -q "Regression analysis" "$REG/fixture_calc_from_usgs.html" \
    || { echo "wheel-smoke FAILED: rendered regression HTML missing expected content" >&2; exit 1; }
grep -q "<svg" "$REG/fixture_calc_from_usgs.svg" \
    || { echo "wheel-smoke FAILED: regression SVG not re-serialized" >&2; exit 1; }
echo "    OK — regression report rendered + sanitized from the dataset"

echo "==> regional neutrality (acceptance criterion 9)"
# The build above used the non-WKCC fixture dataset and a generic SITE_URL, so
# any WKCC/Willamette token in the output is engine leakage — site output must
# contain regional content only when the dataset supplies it. (status.php's CORS
# allow-list used to be the one residual; it now reads HostConfig.allowed_origins
# from runtime-config.json, so no exclusion is needed.)
leaks=$(grep -rilE "wkcc|willamette" "$DOCROOT" || true)
if [ -n "$leaks" ]; then
    echo "$leaks" >&2
    echo "wheel-smoke FAILED: WKCC tokens leaked into a fixture-dataset build" >&2
    exit 1
fi
echo "    OK — fixture-dataset build output carries no WKCC tokens"

echo "==> wheel-smoke PASSED"
