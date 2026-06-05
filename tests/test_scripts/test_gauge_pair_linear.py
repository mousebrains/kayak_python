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
    # Scatter shows all points within the displayed x-range. With n=1000
    # the chart caps x at the 99th percentile of predictor flow, so
    # roughly 1% of points end up off-chart and aren't drawn.
    n_circles = len(re.findall(r"<circle\b", svg))
    assert 980 <= n_circles <= 1000
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


def test_seasonal_residuals_buckets_by_month():
    gpl = _load_script()
    from datetime import date, timedelta

    import numpy as np

    # Two years of daily keys so every season is well populated.
    start = date.fromisoformat("2011-01-01")
    keys = [(start + timedelta(days=i)).isoformat() for i in range(730)]
    # Plant a known +100 cfs bias in Mar/Apr (rain-on-snow), 0 elsewhere.
    resid = np.array([100.0 if int(k[5:7]) in (3, 4) else 0.0 for k in keys])
    y_values = [1000.0] * len(keys)
    fit = gpl.Fit(
        n=len(keys),
        coef_names=["intercept", "x1"],
        coefs=np.array([0.0, 1.0]),
        cov=np.eye(2),
        r2=0.9,
        rmse=1.0,
        sigma_hat=1.0,
        residuals=resid,
        x_means=np.array([0.0]),
    )
    rows = {r["season"]: r for r in gpl.seasonal_residuals(keys, fit, y_values)}
    # Every day lands in exactly one season.
    assert sum(r["n"] for r in rows.values()) == len(keys)
    # The Mar-Apr bucket carries the planted bias; others are unbiased.
    ros_label = next(name for name, months in gpl.SEASONS if set(months) == {3, 4})
    ros = rows[ros_label]
    assert ros["n"] == sum(1 for k in keys if int(k[5:7]) in (3, 4))
    assert abs(ros["mean_residual"] - 100.0) < 1e-9
    assert abs(ros["pct_bias"] - 10.0) < 1e-9  # 100 / 1000
    assert abs(ros["rmse"] - 100.0) < 1e-9
    # Per-season flow magnitude (constant 1000 here) is reported as mean + median.
    assert abs(ros["y_mean"] - 1000.0) < 1e-9
    assert abs(ros["y_median"] - 1000.0) < 1e-9
    for name, months in gpl.SEASONS:
        if set(months) != {3, 4}:
            assert abs(rows[name]["mean_residual"]) < 1e-9


def test_coef_uncertainty_shapes_and_determinism():
    import numpy as np

    gpl = _load_script()
    pred, targ = _toy_series()
    keys = sorted(set(pred) & set(targ))
    xs = [pred[k] for k in keys]
    ys = [targ[k] for k in keys]
    fit = gpl.fit_ols([xs], ys, quadratic=False)
    cu = gpl.coef_uncertainty([xs], ys, keys, False, fit, n_boot=100, seed=0)
    p = len(fit.coefs)
    assert cu.boot_se.shape == (p,)
    assert cu.boot_lo.shape == (p,) and cu.boot_hi.shape == (p,)
    assert np.all(cu.boot_se > 0)
    assert len(cu.vifs) == 1  # single predictor -> VIF ~ 1
    assert abs(cu.vifs[0] - 1.0) < 0.01
    assert -1.0 <= cu.resid_rho1 <= 1.0
    assert cu.n_blocks > 1
    # Deterministic under a fixed seed.
    cu2 = gpl.coef_uncertainty([xs], ys, keys, False, fit, n_boot=100, seed=0)
    assert np.allclose(cu.boot_se, cu2.boot_se)


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


def test_load_leadlag(tmp_path: Path):
    import json

    gpl = _load_script()
    out = tmp_path / "daily.md"
    (tmp_path / "slug.json").write_text(json.dumps({"slug": "slug", "x": 1}))
    assert gpl._load_leadlag("slug", out) == {"slug": "slug", "x": 1}
    assert gpl._load_leadlag("missing", out) is None  # absent file
    assert gpl._load_leadlag(None, out) is None  # no slug
    assert gpl._load_leadlag("slug", None) is None  # no --out


def test_render_leadlag_section():
    gpl = _load_script()
    ll = {
        "slug": "x_leadlag",
        "grid_minutes": 30,
        "n_points": 1000,
        "rmse_valid": True,
        "verdict": "look_ahead",
        "verdict_label": "real signal, but downstream look-ahead only (deployable gain nil)",
        "lags": [
            {
                "site": "111",
                "label": "Up",
                "applied_lag_h": 1.5,
                "corr": 0.56,
                "identifiable": True,
                "deployable": True,
                "note": "",
            },
            {
                "site": "222",
                "label": "Down",
                "applied_lag_h": -2.5,
                "corr": 0.34,
                "identifiable": True,
                "deployable": False,
                "note": "",
            },
            {
                "site": "333",
                "label": "Noise",
                "applied_lag_h": 0.0,
                "corr": 0.04,
                "identifiable": False,
                "deployable": False,
                "note": "",
            },
        ],
        "full": {"gain_pct": 2.2, "ci": [0.46, 3.23], "resolved": True},
        "deploy": {"gain_pct": 0.1, "ci": [-3.19, 2.51], "resolved": False},
    }
    md = "\n".join(gpl._render_leadlag_section(ll))
    assert "## Sub-daily lead/lag" in md
    assert "[`x_leadlag.md`](./x_leadlag.md)" in md  # link to companion
    assert "+1.5" in md and "-2.5" in md  # lag values present
    assert "upstream — deployable" in md and "downstream — look-ahead" in md
    assert "held (not identifiable)" in md
    assert "CI through 0" in md  # deployable not resolved
    assert "look-ahead only" in md  # verdict label embedded


def test_render_leadlag_section_stage_only():
    gpl = _load_script()
    ll = {
        "slug": "y_leadlag",
        "grid_minutes": 30,
        "n_points": 500,
        "rmse_valid": False,
        "verdict": "stage_only",
        "verdict_label": "lags only (RMSE comparison needs flow)",
        "lags": [
            {
                "site": "1",
                "label": "A",
                "applied_lag_h": 1.0,
                "corr": 0.5,
                "identifiable": True,
                "deployable": True,
                "note": "",
            }
        ],
    }
    md = "\n".join(gpl._render_leadlag_section(ll))
    assert "RMSE comparison was omitted" in md
    assert "## Sub-daily lead/lag" in md


def _toy_two_predictor_series() -> tuple[list[str], list[float], list[float], list[float]]:
    """Two non-collinear predictors + target with a planted x1² term.

    y = 50 + 0.8*x1 + 0.3*x2 + 2e-4*x1² (+ small periodic noise), so a
    partial-quadratic fit squaring only x1 recovers all four coefficients.
    """
    from datetime import date, timedelta

    start = date.fromisoformat("2010-01-01")
    keys: list[str] = []
    x1: list[float] = []
    x2: list[float] = []
    ys: list[float] = []
    for i in range(1000):
        keys.append((start + timedelta(days=i)).isoformat())
        a = 200.0 + i
        b = 300.0 + ((i * 37) % 500)
        x1.append(a)
        x2.append(b)
        noise = 10.0 if i % 7 == 0 else -10.0 if i % 7 == 3 else 0.0
        ys.append(50.0 + 0.8 * a + 0.3 * b + 2e-4 * a * a + noise)
    return keys, x1, x2, ys


def test_quad_mask_normalization():
    gpl = _load_script()
    assert gpl._quad_mask(False, 3) == [False, False, False]
    assert gpl._quad_mask(True, 2) == [True, True]
    assert gpl._quad_mask([True, False], 2) == [True, False]
    import pytest

    with pytest.raises(ValueError, match="2 entries for 3 predictors"):
        gpl._quad_mask([True, False], 3)


def test_build_design_matrix_partial_quadratic():
    gpl = _load_script()
    x1 = [1.0, 2.0, 3.0]
    x2 = [4.0, 5.0, 6.0]
    X, names = gpl.build_design_matrix([x1, x2], [True, False])
    assert names == ["intercept", "x1", "x2", "x1^2"]
    assert X.shape == (3, 4)
    assert list(X[:, 3]) == [1.0, 4.0, 9.0]
    # Bool spec still squares every predictor (column order unchanged).
    _, names_all = gpl.build_design_matrix([x1, x2], True)
    assert names_all == ["intercept", "x1", "x2", "x1^2", "x2^2"]


def test_design_row_partial_quadratic():
    gpl = _load_script()
    row = gpl.design_row([10.0, 20.0], [False, True])
    assert list(row) == [1.0, 10.0, 20.0, 400.0]


def test_fit_recovers_partial_quadratic():
    gpl = _load_script()
    _keys, x1, x2, ys = _toy_two_predictor_series()
    fit = gpl.fit_ols([x1, x2], ys, [True, False])
    assert fit.coef_names == ["intercept", "x1", "x2", "x1^2"]
    assert abs(fit.coefs[0] - 50.0) < 5.0
    assert abs(fit.coefs[1] - 0.8) < 0.01
    assert abs(fit.coefs[2] - 0.3) < 0.01
    assert abs(fit.coefs[3] - 2e-4) < 1e-5
    assert fit.r2 > 0.99


def test_render_markdown_partial_quadratic():
    gpl = _load_script()
    keys, x1, x2, ys = _toy_two_predictor_series()
    pred1 = dict(zip(keys, x1, strict=True))
    pred2 = dict(zip(keys, x2, strict=True))
    targ = dict(zip(keys, ys, strict=True))
    quad = [True, False]
    fit = gpl.fit_ols([x1, x2], ys, quad)
    cu = gpl.coef_uncertainty([x1, x2], ys, keys, quad, fit, n_boot=50, seed=0)
    md = gpl.render_markdown(
        name="toy_partial",
        predictor_sites=["111", "222"],
        target_site="999",
        window_start=keys[0],
        window_end=keys[-1],
        quadratic=quad,
        target_data=targ,
        predictor_data=[pred1, pred2],
        overlap_keys=keys,
        window_keys=keys,
        pts_predictors=[x1, x2],
        pts_y=ys,
        fit=fit,
        coef_unc=cu,
        stab=[(keys[0], fit)],
        calc_handles=["p1::111", "p2::222"],
    )
    # Family label names the squared predictor.
    assert "# Multi-Linear+Quadratic(111) regression" in md
    # Reproduce snippet uses --quadratic-for, not --quadratic.
    assert "--quadratic-for 111" in md
    assert "--quadratic\n" not in md
    # Coefficient table labels only x1 as squared.
    assert "(111)²" in md
    assert "(222)²" not in md
    # SQL stub squares only p1.
    assert "p1::111::flow * p1::111::flow" in md
    assert "p2::222::flow * p2::222::flow" not in md


def test_render_fit_json_quadratic_sites():
    gpl = _load_script()
    _keys, x1, x2, ys = _toy_two_predictor_series()
    fit = gpl.fit_ols([x1, x2], ys, [True, False])
    payload = json.loads(
        gpl._render_fit_json(
            slug="toy_partial",
            fit=fit,
            target_site="999",
            predictor_sites=["111", "222"],
            quadratic=[True, False],
            window_start="2010-01-01",
            window_end="2012-09-26",
        )
    )
    assert payload["quadratic"] is True
    assert payload["quadratic_sites"] == ["111"]
    names = [c["name"] for c in payload["coefs"]]
    assert "x1^2 (111)" in names
    assert not any("x2^2" in n for n in names)
    # Bool spec keeps the original schema shape.
    fit_lin = gpl.fit_ols([x1, x2], ys, False)
    payload_lin = json.loads(
        gpl._render_fit_json(
            slug="toy_lin",
            fit=fit_lin,
            target_site="999",
            predictor_sites=["111", "222"],
            quadratic=False,
            window_start="2010-01-01",
            window_end="2012-09-26",
        )
    )
    assert payload_lin["quadratic"] is False
    assert payload_lin["quadratic_sites"] == []


def _render_toy_markdown(gpl: Any, calc_handles: list[str]) -> str:
    """Render a two-predictor linear report with the given handles."""
    keys, x1, x2, ys = _toy_two_predictor_series()
    pred1 = dict(zip(keys, x1, strict=True))
    pred2 = dict(zip(keys, x2, strict=True))
    targ = dict(zip(keys, ys, strict=True))
    fit = gpl.fit_ols([x1, x2], ys, False)
    cu = gpl.coef_uncertainty([x1, x2], ys, keys, False, fit, n_boot=50, seed=0)
    return gpl.render_markdown(
        name="toy_handles",
        predictor_sites=["111", "222"],
        target_site="999",
        window_start=keys[0],
        window_end=keys[-1],
        quadratic=False,
        target_data=targ,
        predictor_data=[pred1, pred2],
        overlap_keys=keys,
        window_keys=keys,
        pts_predictors=[x1, x2],
        pts_y=ys,
        fit=fit,
        coef_unc=cu,
        stab=[(keys[0], fit)],
        calc_handles=calc_handles,
    )


def test_reproduce_snippet_carries_custom_calc_handles():
    # Custom handles are part of the calc_expression output, so the
    # "Generated by" command must carry them for bit-for-bit regeneration.
    gpl = _load_script()
    md = _render_toy_markdown(gpl, ["sm::Foo_merge", "cp::Bar_merge"])
    assert "--calc-handle sm::Foo_merge" in md
    assert "--calc-handle cp::Bar_merge" in md
    # And the calc row uses them.
    assert "sm::Foo_merge::flow" in md


def test_reproduce_snippet_omits_default_calc_handles():
    # Default pX::<site> handles self-reproduce; the snippet stays clean.
    gpl = _load_script()
    md = _render_toy_markdown(gpl, ["p1::111", "p2::222"])
    assert "--calc-handle" not in md


def test_quad_spec_from_args_paths(capsys):
    gpl = _load_script()
    preds = ["111", "222"]
    # Plain flags pass through.
    assert gpl._quad_spec_from_args(False, None, preds) is False
    assert gpl._quad_spec_from_args(True, None, preds) is True
    # --quadratic-for builds a mask in predictor order.
    assert gpl._quad_spec_from_args(False, ["222"], preds) == [False, True]
    # Mutually exclusive with --quadratic.
    assert gpl._quad_spec_from_args(True, ["111"], preds) is None
    assert "mutually exclusive" in capsys.readouterr().err
    # Unknown site rejected by name.
    assert gpl._quad_spec_from_args(False, ["333"], preds) is None
    assert "333" in capsys.readouterr().err


def test_deploy_note_renders_and_reproduces():
    # A fit whose deployed expression differs from the fitted one (e.g. the
    # estimate is summed with a live gauge) carries a warning in the
    # calc_expression section AND the note in the Reproduce snippet,
    # shell-quoted, so a regen keeps the warning.
    gpl = _load_script()
    keys, x1, x2, ys = _toy_two_predictor_series()
    pred1 = dict(zip(keys, x1, strict=True))
    pred2 = dict(zip(keys, x2, strict=True))
    targ = dict(zip(keys, ys, strict=True))
    fit = gpl.fit_ols([x1, x2], ys, False)
    cu = gpl.coef_uncertainty([x1, x2], ys, keys, False, fit, n_boot=50, seed=0)
    note = "deployed row adds live Johnson: use 1.177*jo, not 0.177*jo."
    md = gpl.render_markdown(
        name="toy_deploy",
        predictor_sites=["111", "222"],
        target_site="999",
        window_start=keys[0],
        window_end=keys[-1],
        quadratic=False,
        target_data=targ,
        predictor_data=[pred1, pred2],
        overlap_keys=keys,
        window_keys=keys,
        pts_predictors=[x1, x2],
        pts_y=ys,
        fit=fit,
        coef_unc=cu,
        stab=[(keys[0], fit)],
        calc_handles=["p1::111", "p2::222"],
        deploy_note=note,
    )
    assert "Deployment note" in md
    assert note in md
    # Shell-quoted in the Reproduce snippet (note contains spaces + '*').
    assert "--deploy-note 'deployed row adds live Johnson" in md
    # Absent by default.
    md_plain = _render_toy_markdown(gpl, ["p1::111", "p2::222"])
    assert "Deployment note" not in md_plain
