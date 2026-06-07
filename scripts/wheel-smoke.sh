#!/usr/bin/env bash
# Wheel smoke test (dataset-separation S4a-2 slice C).
#
# Builds the kayak wheel, installs it into a fresh venv OUTSIDE the repo
# checkout, and exercises the packaged engine end-to-end: every runtime
# resource (data YAMLs, schema migrations, web static assets, the PHP layer,
# install templates, LICENSE) must resolve via importlib.resources from
# site-packages — NOT the source tree — and `levels init-db` + `levels build`
# must produce a deployed site from them.
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
checks = [
    (("data",), "sources.yaml"),
    (("data",), "builder.yaml"),
    (("data", "db", "migrations"), "0001_baseline.sql"),
    (("data", "db", "migrations"), "manifest.csv"),
    (("web", "static"), "map.js"),
    (("web", "static"), "leaflet.js"),
    (("web", "static", "images"), "marker-icon.png"),
    (("web", "php"), "latest.php"),
    (("web", "php", "includes"), "db.php"),
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

echo "==> levels init-db (packaged schema + migrations) → $DB"
DATABASE_URL="sqlite:///$DB" "$LEVELS" init-db >/dev/null
test -f "$DB"
echo "    OK — schema created"

echo "==> levels build (packaged data + web layer) → $DOCROOT"
DATABASE_URL="sqlite:///$DB" OUTPUT_DIR="$DOCROOT" "$LEVELS" build >/dev/null
# The deployed site must carry assets sourced from every packaged tree.
missing=0
for rel in \
    index.html \
    style.css \
    sw.js \
    static/map.js \
    static/leaflet.js \
    latest.php \
    includes/db.php \
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

echo "==> wheel-smoke PASSED"
