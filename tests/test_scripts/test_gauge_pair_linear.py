"""Unit tests for scripts/regression/gauge_pair_linear.py.

The script lives outside src/ so we import it via importlib path. Live
USGS fetches are stubbed with monkeypatch — these tests run fully
offline against a small synthetic dataset.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "regression" / "gauge_pair_linear.py"
)


def _load_script() -> Any:
    # Cache the loaded module so repeated test calls don't re-exec, and so
    # @dataclass on Python 3.14+ can look up the owning module in sys.modules
    # while resolving ClassVar/InitVar annotations.
    name = "gauge_pair_linear_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _SCRIPT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _toy_series() -> tuple[dict[str, float], dict[str, float]]:
    """Build a synthetic predictor/target pair with a known linear fit.

    Predictor: 200..1199 cfs, one value per day for 1000 days.
    Target: 0.8 * predictor + 50 + small noise.
    """
    base = "2010-01-01"
    from datetime import date, timedelta

    start = date.fromisoformat(base)
    pred: dict[str, float] = {}
    targ: dict[str, float] = {}
    for i in range(1000):
        day = (start + timedelta(days=i)).isoformat()
        x = 200.0 + i
        pred[day] = x
        # Sinusoidal residual ensures non-zero RSS but doesn't bias the slope.
        targ[day] = 0.8 * x + 50.0 + (10.0 if i % 7 == 0 else -10.0 if i % 7 == 3 else 0.0)
    return pred, targ


def test_fit_recovers_known_slope_and_intercept():
    gpl = _load_script()
    pred, targ = _toy_series()
    keys = sorted(set(pred) & set(targ))
    xs = [pred[k] for k in keys]
    ys = [targ[k] for k in keys]
    fit = gpl.fit_ols([xs], ys, quadratic=False)
    # Slope and intercept should be near the planted values.
    assert abs(fit.coefs[0] - 50.0) < 5.0
    assert abs(fit.coefs[1] - 0.8) < 0.01
    assert fit.r2 > 0.99
    assert fit.coef_names == ["intercept", "x1"]
    assert fit.cov.shape == (2, 2)


def test_render_fit_json_schema(tmp_path: Path):
    gpl = _load_script()
    pred, targ = _toy_series()
    keys = sorted(set(pred) & set(targ))
    xs = [pred[k] for k in keys]
    ys = [targ[k] for k in keys]
    fit = gpl.fit_ols([xs], ys, quadratic=False)
    raw = gpl._render_fit_json(
        slug="toy",
        fit=fit,
        target_site="TARGET",
        predictor_sites=["PRED"],
        quadratic=False,
        window_start="2010-01-01",
        window_end="2012-09-26",
    )
    payload = json.loads(raw)
    # Schema check.
    assert payload["slug"] == "toy"
    assert payload["target"] == "TARGET"
    assert payload["predictors"] == ["PRED"]
    assert payload["quadratic"] is False
    assert payload["n"] == len(xs)
    assert payload["window"] == ["2010-01-01", "2012-09-26"]
    assert isinstance(payload["coefs"], list) and len(payload["coefs"]) == 2
    assert {c["name"] for c in payload["coefs"]} >= {"intercept"}
    assert all({"name", "value", "se"} <= set(c.keys()) for c in payload["coefs"])
    # Cov matrix is 2x2.
    assert len(payload["cov"]) == 2 and len(payload["cov"][0]) == 2
    # r2/RMSE/sigma_hat present.
    for key in ("r2", "rmse", "sigma_hat"):
        assert isinstance(payload[key], float)


def test_render_residuals_svg_well_formed(tmp_path: Path):
    gpl = _load_script()
    pred, targ = _toy_series()
    keys = sorted(set(pred) & set(targ))
    xs = [pred[k] for k in keys]
    ys = [targ[k] for k in keys]
    fit = gpl.fit_ols([xs], ys, quadratic=False)
    svg = gpl._render_residuals_svg(
        slug="toy",
        fit=fit,
        pivot_predictor=xs,
        target_site="14328000",
        predictor_site="14330000",
    )
    # Parses as XML.
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    # Has a scatter point per data point (n=1000 < max_points=1500 so all kept).
    n_circles = len(re.findall(r"<circle\b", svg))
    assert n_circles == 1000
    # Has the title and axis labels.
    assert "14328000" in svg
    assert "14330000" in svg
    assert "USGS 14330000 flow (cfs)" in svg
    assert "residual" in svg.lower()


def test_render_residuals_svg_subsamples_above_threshold():
    gpl = _load_script()
    # Build a larger synthetic dataset to trigger the random subsample.
    from datetime import date, timedelta

    pred: dict[str, float] = {}
    targ: dict[str, float] = {}
    start = date.fromisoformat("2010-01-01")
    for i in range(3000):
        d = (start + timedelta(days=i)).isoformat()
        x = 500.0 + i
        pred[d] = x
        targ[d] = 0.5 * x + 10.0
    keys = sorted(set(pred) & set(targ))
    xs = [pred[k] for k in keys]
    ys = [targ[k] for k in keys]
    fit = gpl.fit_ols([xs], ys, quadratic=False)
    svg = gpl._render_residuals_svg(
        slug="sub",
        fit=fit,
        pivot_predictor=xs,
        target_site="t",
        predictor_site="p",
    )
    n_circles = len(re.findall(r"<circle\b", svg))
    # Subsample cap is 1500.
    assert n_circles == 1500


def test_nice_axis_round_ticks():
    import math

    gpl = _load_script()
    lo, hi, step = gpl._nice_axis(123.0, 9876.0)
    assert lo <= 123.0
    assert hi >= 9876.0
    # Step is a "nice" round value: 1/2/5 * 10^k.
    mantissa = step / 10 ** math.floor(math.log10(step))
    assert any(abs(mantissa - m) < 1e-6 for m in (1.0, 2.0, 5.0))


def test_predict_returns_three_uncertainties():
    gpl = _load_script()
    pred, targ = _toy_series()
    keys = sorted(set(pred) & set(targ))
    xs = [pred[k] for k in keys]
    ys = [targ[k] for k in keys]
    fit = gpl.fit_ols([xs], ys, quadratic=False)
    y_hat, se_mean, se_pred = gpl.predict(fit, [700.0], quadratic=False)
    # y_hat near 0.8 * 700 + 50 = 610
    assert abs(y_hat - 610.0) < 5.0
    # Prediction SE is strictly larger than mean-response SE.
    assert se_pred > se_mean > 0.0
