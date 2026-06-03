"""Unit tests for scripts/regression/gauge_lead_lag.py.

Loaded via importlib (the script lives outside src/). All tests run fully
offline — no USGS fetch — against small synthetic series.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "regression" / "gauge_lead_lag.py"
_EPOCH = datetime(1970, 1, 1)


def _load() -> Any:
    name = "gauge_lead_lag_under_test"
    if name in sys.modules:
        return sys.modules[name]
    # The script imports its sibling `_resample` module; put the script dir on
    # sys.path so that resolves when we exec it outside its own directory.
    if str(_SCRIPT_PATH.parent) not in sys.path:
        sys.path.insert(0, str(_SCRIPT_PATH.parent))
    spec = importlib.util.spec_from_file_location(name, _SCRIPT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _utc_epoch(s: str) -> int:
    return int((datetime.strptime(s, "%Y-%m-%d %H:%M") - _EPOCH).total_seconds())


def test_parse_iv_rdb_tz_and_column_pick():
    gll = _load()
    # Header carries a stage (_00065) column *before* discharge (_00060) to
    # check the parser selects discharge by name, not by position. Includes a
    # PST row, a PDT row, an "Ice" quality flag (skipped), and the 5s/15s
    # format-spec row (skipped).
    rdb = (
        "# comment line\n"
        "agency_cd\tsite_no\tdatetime\ttz_cd\t99_00065\t99_00065_cd\t99_00060\t99_00060_cd\n"
        "5s\t15s\t20d\t6s\t14n\t10s\t14n\t10s\n"
        "USGS\t14159000\t1988-01-01 00:00\tPST\t3.10\tA\t1330\tA\n"
        "USGS\t14159000\t1988-01-01 00:30\tPST\t3.10\tA\tIce\tA\n"
        "USGS\t14159000\t1988-07-01 12:00\tPDT\t2.50\tA\t900\tA\n"
    )
    out = dict(gll._parse_iv_rdb(rdb))
    # PST = UTC-8: local 00:00 -> 08:00 UTC; PDT = UTC-7: local 12:00 -> 19:00.
    assert out[_utc_epoch("1988-01-01 08:00")] == 1330.0
    assert out[_utc_epoch("1988-07-01 19:00")] == 900.0
    # "Ice" row dropped; only the two numeric rows survive.
    assert len(out) == 2


def _sine_series(n: int) -> list[float]:
    # Deterministic, non-periodic-looking; gives varied first differences so
    # the cross-correlation has a sharp, unambiguous peak.
    return [math.sin(0.30 * i) + math.sin(0.07 * i) for i in range(n)]


def test_ccf_curve_recovers_known_lead():
    gll = _load()
    n, lead = 400, 3
    base = _sine_series(n + lead + 2)
    hour = gll.SECONDS_PER_HOUR
    # Target is the base series; predictor is the SAME series shifted so it
    # leads the target by `lead` hours: predictor[h] == target[h + lead].
    target = {i * hour: base[i] for i in range(n)}
    pred = {i * hour: base[i + lead] for i in range(n)}
    curve = gll.ccf_curve(target, pred, range(-6, 7))
    res = gll.classify_lag("PRED", curve)
    assert res.identifiable
    assert res.best_lag_h == lead
    assert res.applied_lag_h == lead
    assert res.best_corr > 0.99


def test_classify_lag_holds_unidentifiable_contemporaneous():
    gll = _load()
    # Two independent sine frequencies -> first differences nearly orthogonal,
    # so no lag clears the identifiability floor.
    hour = gll.SECONDS_PER_HOUR
    a = [math.sin(0.30 * i) for i in range(400)]
    b = [math.sin(0.011 * i + 1.7) for i in range(400)]
    target = {i * hour: a[i] for i in range(400)}
    pred = {i * hour: b[i] for i in range(400)}
    res = gll.classify_lag("NOISE", gll.ccf_curve(target, pred, range(-6, 7)))
    assert res.best_corr < gll.MIN_IDENTIFIABLE_CORR
    assert res.identifiable is False
    assert res.applied_lag_h == 0
    assert "held contemporaneous" in res.travel_note


def test_classify_lag_empty_curve():
    gll = _load()
    res = gll.classify_lag("X", [])
    assert res.identifiable is False
    assert res.applied_lag_h == 0
    assert math.isnan(res.best_corr)


def test_ols_and_eval_recover_known_fit():
    import numpy as np

    gll = _load()
    x1 = np.linspace(100, 500, 300)
    # x2 independent of x1 (not collinear) so the coefficients are identifiable.
    x2 = np.array([(i * i) % 37 for i in range(300)], dtype=float)
    y = 2.0 + 0.5 * x1 - 1.5 * x2  # exact linear relationship
    coefs = gll.ols([x1, x2], y)
    assert abs(coefs[0] - 2.0) < 1e-6
    assert abs(coefs[1] - 0.5) < 1e-6
    assert abs(coefs[2] + 1.5) < 1e-6
    rmse, r2 = gll.eval_fit([x1, x2], y, coefs)
    assert rmse < 1e-6
    assert r2 > 0.999999


def test_render_ccf_svg_well_formed():
    gll = _load()
    results = [
        gll.LagResult(
            "14158850", 2, 0.71, [(-2, 0.1), (0, 0.4), (2, 0.71), (4, 0.5)], True, 2, "up"
        ),
        gll.LagResult("14159500", -17, 0.04, [(-2, 0.02), (0, 0.03), (2, 0.04)], False, 0, "n/a"),
    ]
    svg = gll._render_ccf_svg(results)
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    # Identifiable series gets a peak marker; unidentifiable is dashed, no marker.
    assert "stroke-dasharray" in svg  # the unidentifiable curve
    assert "14158850" in svg and "14159500" in svg


def test_render_ccf_svg_degenerate_inputs_dont_crash():
    gll = _load()
    # Empty results -> placeholder svg, still valid XML.
    ET.fromstring(gll._render_ccf_svg([]))
    # Single lag + all-zero correlations: the axis guards must avoid div-by-zero.
    flat = [gll.LagResult("X", 0, 0.0, [(0, 0.0)], False, 0, "n/a")]
    ET.fromstring(gll._render_ccf_svg(flat))


def test_eval_fit_empty_returns_nan():
    import math

    import numpy as np

    gll = _load()
    rmse, r2 = gll.eval_fit([np.array([])], np.array([]), np.array([0.0, 1.0]))
    assert math.isnan(rmse) and math.isnan(r2)
