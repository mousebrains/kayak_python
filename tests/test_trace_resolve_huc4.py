"""Unit tests for ``kayak.tracing.trace._resolve_huc4``.

Covers the HUC4 auto-detect logic, in particular the divide-disagreement
branch — when the put-in and take-out resolve to different HUC4s, the endpoint
nearer its own flowline wins (the mis-detection bug fixed in #23; see memory
``trace_huc4_and_toolchain``).

``trace.py`` imports the GDAL bindings (``osgeo``) at module load and CI has no
system GDAL, so we stub ``osgeo`` before importing. ``_resolve_huc4`` itself
only calls ``find_huc4_with_distance``, which each test monkeypatches — no GDAL
is exercised.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

# trace.py imports the GDAL bindings (osgeo) at module load. On a dev box with
# GDAL installed, use the real module — stubbing it would pollute sys.modules
# and break tests/test_tracing/test_trace.py, which exercises the real osgeo.
# Only when GDAL is absent (CI) do we stub it: _resolve_huc4 itself never
# touches osgeo, only find_huc4_with_distance, which each test monkeypatches.
try:
    import osgeo  # noqa: F401  — real GDAL on a dev box; used as-is

    _stubbed_osgeo = False
except ImportError:
    sys.modules["osgeo"] = MagicMock()
    sys.modules["osgeo.ogr"] = MagicMock()
    _stubbed_osgeo = True

from kayak.tracing import trace

if _stubbed_osgeo:
    # Drop the stub so it can't shadow the real osgeo for other tests
    # (tests/test_tracing/test_trace.py importorskips it). trace.py has already
    # captured its reference at import, and _resolve_huc4 never calls osgeo.
    sys.modules.pop("osgeo", None)
    sys.modules.pop("osgeo.ogr", None)

PUTIN = (42.0, -116.0)
TAKEOUT = (42.5, -116.5)


def _patch_find(monkeypatch: pytest.MonkeyPatch, results: dict) -> None:
    """Patch find_huc4_with_distance to return results[(lat, lon)] = (huc4, dist)."""

    def fake(lat: float, lon: float, buffer_deg: float = 0.15) -> tuple:
        return results[(lat, lon)]

    monkeypatch.setattr(trace, "find_huc4_with_distance", fake)


def test_explicit_huc4_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit --huc4 is returned verbatim; no detection runs."""
    called = False

    def fake(*_a: object, **_k: object) -> tuple:
        nonlocal called
        called = True
        return (None, float("inf"))

    monkeypatch.setattr(trace, "find_huc4_with_distance", fake)
    assert trace._resolve_huc4("1705", PUTIN, TAKEOUT, lambda *_: None) == "1705"
    assert not called


def test_endpoints_agree(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both endpoints resolve to the same HUC4 → that HUC4, no warning."""
    _patch_find(monkeypatch, {PUTIN: ("1705", 0.01), TAKEOUT: ("1705", 0.02)})
    logs: list[str] = []
    assert trace._resolve_huc4(None, PUTIN, TAKEOUT, logs.append) == "1705"
    assert not any("WARNING" in m for m in logs)


def test_disagreement_putin_nearer_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Endpoints disagree; put-in is nearer its flowline (dp < dt) → put-in's HUC4."""
    _patch_find(monkeypatch, {PUTIN: ("1705", 0.001), TAKEOUT: ("1601", 0.05)})
    logs: list[str] = []
    assert trace._resolve_huc4(None, PUTIN, TAKEOUT, logs.append) == "1705"
    assert any("WARNING" in m for m in logs)


def test_disagreement_takeout_nearer_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Endpoints disagree; take-out is nearer (dt < dp) → take-out's HUC4."""
    _patch_find(monkeypatch, {PUTIN: ("1705", 0.04), TAKEOUT: ("1601", 0.002)})
    assert trace._resolve_huc4(None, PUTIN, TAKEOUT, lambda *_: None) == "1601"


def test_only_one_endpoint_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    """One endpoint off-network (None) → the other's HUC4, no disagreement."""
    _patch_find(monkeypatch, {PUTIN: (None, float("inf")), TAKEOUT: ("1705", 0.03)})
    assert trace._resolve_huc4(None, PUTIN, TAKEOUT, lambda *_: None) == "1705"


def test_neither_resolves_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither endpoint resolves → ValueError."""
    _patch_find(monkeypatch, {PUTIN: (None, float("inf")), TAKEOUT: (None, float("inf"))})
    with pytest.raises(ValueError, match="Could not find HUC4"):
        trace._resolve_huc4(None, PUTIN, TAKEOUT, lambda *_: None)
