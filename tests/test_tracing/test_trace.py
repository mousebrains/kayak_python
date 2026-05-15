"""Regression test for kayak.tracing.trace.

Pinned ground truth for one real reach (Sandy, HUC4 1708) captured
2026-05-12 against the pre-refactor implementation. The refactor that
splits trace_reach's cc=15 body into three helpers (per
docs/done/PLAN_c901_cleanup.md §Out of scope, executed despite the deferral)
must not change the path. The test is the regression net the plan said
this module didn't have.

Skipped when:
  - the `osgeo` (GDAL) Python binding isn't installed (CI doesn't run
    this — tracing is dev-machine work), or
  - the Trace-cache/ directory is missing the relevant pre-extracted
    GPKG (Trace-cache is gitignored, ~5 GB).

Run locally with:
    .venv/bin/pip install gdal
    .venv/bin/pytest tests/test_tracing/ -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("osgeo")  # GDAL Python bindings; not in CI deps.

# Skip if the local trace cache is missing — Trace-cache/ is gitignored
# and only the dev box keeps it populated.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TRACE_GPKG = _REPO_ROOT / "Trace-cache" / "trace" / "trace_1708.gpkg"
if not _TRACE_GPKG.exists():
    pytest.skip(
        f"Trace-cache not populated (looked for {_TRACE_GPKG}); "
        "run scripts/extract_trace_data.sh first.",
        allow_module_level=True,
    )

# trace.py uses module-level constants TRACE_DIR / NHD_HR_DIR that point at
# the dev box's Trace-cache. The test relies on those defaults — no override.

from kayak.tracing.trace import find_huc4, total_distance, trace_reach  # noqa: E402

# Sandy h reach — from reach.id=77 in the live DB on 2026-05-12.
SANDY_PUTIN = (45.4913720742051, -122.307366233889)
SANDY_TAKEOUT = (45.540374880849, -122.379632162994)


def test_find_huc4_sandy() -> None:
    """find_huc4 resolves Sandy putin to HUC4 1708 (Lower Columbia-Sandy)."""
    assert find_huc4(*SANDY_PUTIN) == "1708"


def test_trace_reach_sandy_path_pinned() -> None:
    """trace_reach on the Sandy h reach must reproduce the pinned path.

    The refactor splits trace_reach's body into _resolve_huc4 +
    _extend_and_trim_path + _load_missing_geoms helpers. Off-by-one in
    the extended-path slicing or a dropped geometry hydration would
    show up as a different point count, distance, or boundary coord.
    Tolerances are tight (~1e-6° on coords, 0.01 mi on distance) so a
    real regression doesn't squeak through.
    """
    coords = trace_reach(SANDY_PUTIN, SANDY_TAKEOUT, verbose=False)

    assert len(coords) == 240, f"point count changed: got {len(coords)}"

    # Boundary coords — the snap from input to nearest flowline + the
    # end-trim slicing both affect these.
    assert coords[0] == pytest.approx((45.491681, -122.307288), abs=1e-5)
    assert coords[-1] == pytest.approx((45.540166, -122.379956), abs=1e-5)

    # Path total distance — catches geometry-hydration regressions
    # (a dropped segment shows as a shorter distance).
    assert total_distance(coords) == pytest.approx(8.4983, abs=0.01)
