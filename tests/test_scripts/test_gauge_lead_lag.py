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


_RDB = (
    "# comment line\n"
    "agency_cd\tsite_no\tdatetime\ttz_cd\t99_00065\t99_00065_cd\t99_00060\t99_00060_cd\n"
    "5s\t15s\t20d\t6s\t14n\t10s\t14n\t10s\n"
    "USGS\t14159000\t1988-01-01 00:00\tPST\t3.10\tA\t1330\tA\n"
    "USGS\t14159000\t1988-01-01 00:30\tPST\t3.20\tA\tIce\tA\n"
    "USGS\t14159000\t1988-07-01 12:00\tPDT\t2.50\tA\t900\tA\n"
)


def test_parse_iv_rdb_param_selection_and_tz():
    gll = _load()
    # Flow column (00060): the "Ice" row drops; PST->+8, PDT->+7.
    flow = dict(gll._parse_iv_rdb(_RDB, "00060"))
    assert flow[_utc_epoch("1988-01-01 08:00")] == 1330.0
    assert flow[_utc_epoch("1988-07-01 19:00")] == 900.0
    assert len(flow) == 2
    # Stage column (00065): all three rows are numeric, so all survive.
    stage = dict(gll._parse_iv_rdb(_RDB, "00065"))
    assert stage[_utc_epoch("1988-01-01 08:00")] == 3.10
    assert len(stage) == 3


def test_available_params():
    gll = _load()
    assert gll._available_params(_RDB) == {"00060", "00065"}
    stage_only = _RDB.replace("99_00060", "99_zzzzz")  # hide the flow column
    assert gll._available_params(stage_only) == {"00065"}


def _sine_series(n: int) -> list[float]:
    return [math.sin(0.30 * i) + math.sin(0.07 * i) for i in range(n)]


def test_ccf_curve_recovers_upstream_lead():
    gll = _load()
    n, lead = 400, 3
    base = _sine_series(n + lead + 2)
    step = gll.SECONDS_PER_HOUR
    # Predictor LEADS the target (upstream): predictor[g] == target[g + lead].
    target = {i * step: base[i] for i in range(n)}
    pred = {i * step: base[i + lead] for i in range(n)}
    res = gll.classify_lag("PRED", gll.ccf_curve(target, pred, max_steps=6, step=step), step)
    assert res.identifiable
    assert res.best_lag_steps == lead
    assert res.best_lag_h == float(lead)
    assert res.applied_lag_steps == lead
    assert res.deployable is True  # upstream -> past read -> deployable
    assert res.best_corr > 0.99


def test_ccf_curve_recovers_downstream_lag_not_deployable():
    gll = _load()
    n, lag = 400, 4
    base = _sine_series(n + lag + 2)
    step = gll.SECONDS_PER_HOUR
    # Predictor LAGS the target (downstream): predictor[g] == target[g - lag].
    target = {i * step: base[i + lag] for i in range(n)}
    pred = {i * step: base[i] for i in range(n)}
    res = gll.classify_lag("PRED", gll.ccf_curve(target, pred, max_steps=8, step=step), step)
    assert res.identifiable
    assert res.best_lag_steps == -lag  # negative = downstream
    assert res.deployable is False  # future read -> not deployable
    assert "not deployable" in res.travel_note


def test_classify_lag_holds_unidentifiable_contemporaneous():
    gll = _load()
    step = gll.SECONDS_PER_HOUR
    a = [math.sin(0.30 * i) for i in range(400)]
    b = [math.sin(0.011 * i + 1.7) for i in range(400)]
    target = {i * step: a[i] for i in range(400)}
    pred = {i * step: b[i] for i in range(400)}
    res = gll.classify_lag("NOISE", gll.ccf_curve(target, pred, max_steps=6, step=step), step)
    assert res.best_corr < gll.MIN_IDENTIFIABLE_CORR
    assert res.identifiable is False
    assert res.applied_lag_steps == 0
    assert res.deployable is False
    assert "held contemporaneous" in res.travel_note


def test_classify_lag_empty_curve():
    gll = _load()
    res = gll.classify_lag("X", [], gll.SECONDS_PER_HOUR)
    assert res.identifiable is False
    assert res.applied_lag_steps == 0
    assert math.isnan(res.best_corr)


def test_half_hour_grid_resolves_fractional_lag():
    gll = _load()
    # On a 30-min grid, a 3-step lead is +1.5 h — exercises fractional reporting.
    n, lead = 400, 3
    base = _sine_series(n + lead + 2)
    step = 1800
    target = {i * step: base[i] for i in range(n)}
    pred = {i * step: base[i + lead] for i in range(n)}
    res = gll.classify_lag("P", gll.ccf_curve(target, pred, max_steps=8, step=step), step)
    assert res.best_lag_steps == lead
    assert abs(res.best_lag_h - 1.5) < 1e-9


def test_ols_and_eval_recover_known_fit():
    import numpy as np

    gll = _load()
    x1 = np.linspace(100, 500, 300)
    x2 = np.array([(i * i) % 37 for i in range(300)], dtype=float)
    y = 2.0 + 0.5 * x1 - 1.5 * x2
    coefs = gll.ols([x1, x2], y)
    assert abs(coefs[0] - 2.0) < 1e-6
    assert abs(coefs[1] - 0.5) < 1e-6
    assert abs(coefs[2] + 1.5) < 1e-6
    rmse, r2 = gll.eval_fit([x1, x2], y, coefs)
    assert rmse < 1e-6
    assert r2 > 0.999999


def _lag(gll, site, steps, corr, identifiable, applied):
    return gll.LagResult(
        site, steps, corr, [(-2.0, 0.1), (0.0, 0.4), (1.5, corr)], identifiable, applied, 1800, "n"
    )


def test_render_ccf_svg_well_formed():
    gll = _load()
    results = [_lag(gll, "14158850", 3, 0.71, True, 3), _lag(gll, "14159500", -34, 0.04, False, 0)]
    svg = gll._render_ccf_svg(results)
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    assert "stroke-dasharray" in svg  # the unidentifiable curve is dashed
    assert "14158850" in svg and "14159500" in svg
    assert "+1.5 h" in svg  # fractional applied lag in the legend (3 * 30min)


def test_render_ccf_svg_degenerate_inputs_dont_crash():
    gll = _load()
    ET.fromstring(gll._render_ccf_svg([]))
    flat = [gll.LagResult("X", 0, 0.0, [(0.0, 0.0)], False, 0, 1800, "n/a")]
    ET.fromstring(gll._render_ccf_svg(flat))


def test_eval_fit_empty_returns_nan():
    import numpy as np

    gll = _load()
    rmse, r2 = gll.eval_fit([np.array([])], np.array([]), np.array([0.0, 1.0]))
    assert math.isnan(rmse) and math.isnan(r2)


def test_classify_verdict_branches():
    gll = _load()
    cv = gll.classify_verdict
    assert cv(any_lag=False, deploy_sig=True, deploy_gain=5.0, full_sig=True) == "no_lag"
    assert cv(any_lag=True, deploy_sig=True, deploy_gain=5.0, full_sig=True) == "usable"
    # The review-caught case: deployable CI resolved but the gain is < 2% (or
    # resolved-negative) -> its own branch, not the contradictory "unresolved".
    assert (
        cv(any_lag=True, deploy_sig=True, deploy_gain=1.0, full_sig=True) == "deployable_immaterial"
    )
    assert (
        cv(any_lag=True, deploy_sig=True, deploy_gain=-3.0, full_sig=False)
        == "deployable_immaterial"
    )
    # full resolved, deployable not -> downstream look-ahead.
    assert cv(any_lag=True, deploy_sig=False, deploy_gain=0.1, full_sig=True) == "look_ahead"
    # neither resolved.
    assert cv(any_lag=True, deploy_sig=False, deploy_gain=0.1, full_sig=False) == "unresolved"
    # Every verdict key has a label.
    for k in (
        "no_lag",
        "usable",
        "deployable_immaterial",
        "look_ahead",
        "unresolved",
        "stage_only",
    ):
        assert k in gll.VERDICT_LABEL
