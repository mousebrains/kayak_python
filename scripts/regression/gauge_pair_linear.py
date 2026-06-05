#!/usr/bin/env python3
"""Fit a (multi-)linear regression of one USGS gauge against one or more others.

Used to derive `calc_expression` formulas that replace a retired or
intermittent gauge with an estimate from still-active gauges. Produces
a markdown analysis report (window stability, parameter covariance,
residual diagnostics) plus the `calc_expression` column values ready to
paste into a new `calc_expression.csv` row in the `kayak_data` repo.

Common case is single-predictor linear:

    python3 scripts/regression/gauge_pair_linear.py \\
        --predictor 14330000 --target 14328000 \\
        --start 1985-01-01 --end 2024-06-09 \\
        --name rogue_14328000_from_14330000 \\
        --out  docs/regression/rogue_14328000_from_14330000.md

Multi-linear (use multiple --predictor flags). Each predictor adds a
column to the design matrix:

    python3 scripts/regression/gauge_pair_linear.py \\
        --predictor 14330000 --predictor 14337600 \\
        --target 14328000 --start 1985-01-01 --end 2024-06-09 \\
        --name rogue_14328000_multi --out docs/regression/...md

Add a quadratic term per predictor with --quadratic:

    ... --predictor 14330000 --quadratic --target ...
    # fits target = b0 + b1 * x + b2 * x^2

Or square only selected predictors with --quadratic-for (repeatable;
mutually exclusive with --quadratic). Useful when the residual curvature
tracks one predictor and the other's x² term is not significant:

    ... --predictor 14307620 --predictor 14325000 \\
        --quadratic-for 14307620 --target ...
    # fits target = b0 + b1 * x1 + b2 * x2 + b3 * x1^2

Future: piecewise-linear by predictor flow regime (low/normal/high) is
worth exploring if a single linear/quadratic fit leaves systematic
residuals in the top or bottom quintiles; see "Future" in the writeup.

Standalone — depends on numpy + curl + Python stdlib. No kayak imports,
so future maintainers can run it without the project venv.
"""

from __future__ import annotations

import argparse
import contextlib
import json as _json
import math
import random
import shlex
import statistics
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from _resample import block_bootstrap, lag1_autocorr, vif


@dataclass(frozen=True)
class Fit:
    """OLS fit with full parameter covariance.

    Coefficients are in `coef_names` order; `coefs[0]` is the intercept
    when an intercept is included (always the case here).
    """

    n: int
    coef_names: list[str]
    coefs: np.ndarray  # shape (p,) — intercept first
    cov: np.ndarray  # shape (p, p)
    r2: float
    rmse: float  # plain sqrt(RSS/n)
    sigma_hat: float  # sqrt(RSS/(n-p)) unbiased
    residuals: np.ndarray  # shape (n,)
    x_means: np.ndarray  # shape (p-1,) means of the raw predictor columns (no intercept, no quad)
    x_ranges: list[tuple[float, float]] = field(default_factory=list)
    y_mean: float = 0.0
    y_range: tuple[float, float] = (0.0, 0.0)

    @property
    def se(self) -> np.ndarray:
        return np.sqrt(np.diag(self.cov))

    @property
    def corr(self) -> np.ndarray:
        s = self.se
        return self.cov / np.outer(s, s)


def fetch_daily_means(site_no: str) -> dict[str, float]:
    """USGS daily-mean discharge (cfs) for one site, full period of record.

    Caches to /tmp/<site_no>_dv.tsv so reruns don't re-download.
    """
    cache = Path(f"/tmp/{site_no}_dv.tsv")
    if not cache.exists() or cache.stat().st_size < 1000:
        url = (
            "https://waterservices.usgs.gov/nwis/dv/"
            f"?format=rdb&sites={site_no}"
            "&startDT=1900-01-01&endDT=2099-12-31"
            "&parameterCd=00060&statCd=00003"
        )
        subprocess.run(["curl", "-sL", url, "-o", str(cache)], check=True)
    out: dict[str, float] = {}
    for line in cache.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if parts[0] == "agency_cd" or parts[0].startswith("5s"):
            continue
        if len(parts) < 4:
            continue
        with contextlib.suppress(ValueError):
            out[parts[2]] = float(parts[3])
    if not out:
        raise RuntimeError(f"No daily means parsed from {cache}")
    return out


def _quad_mask(quadratic: bool | Sequence[bool], n_predictors: int) -> list[bool]:
    """Normalize the quadratic spec to a per-predictor mask.

    `quadratic` is either a bool (x² for all predictors / none — the
    original CLI flag) or a per-predictor mask (the --quadratic-for form).
    """
    if isinstance(quadratic, bool):
        return [quadratic] * n_predictors
    mask = list(quadratic)
    if len(mask) != n_predictors:
        raise ValueError(f"quadratic mask has {len(mask)} entries for {n_predictors} predictors")
    return mask


def build_design_matrix(
    predictors: list[list[float]],
    quadratic: bool | Sequence[bool],
) -> tuple[np.ndarray, list[str]]:
    """Stack intercept, linear, and optional quadratic columns.

    `predictors` is a list of length-n columns, one per predictor gauge.
    Column order in the returned matrix: [1, x1, x2, ..., x1², x2², ...]
    (quadratics last so they're easy to read off the coefficient vector).
    Squared columns are emitted only for predictors selected by
    `quadratic` (bool = all/none; sequence = per-predictor mask), in
    predictor order.
    """
    n = len(predictors[0])
    quad = _quad_mask(quadratic, len(predictors))
    cols: list[np.ndarray] = [np.ones(n)]
    names = ["intercept"]
    for i, p in enumerate(predictors, start=1):
        cols.append(np.asarray(p, dtype=float))
        names.append(f"x{i}")
    for i, p in enumerate(predictors, start=1):
        if quad[i - 1]:
            cols.append(np.asarray(p, dtype=float) ** 2)
            names.append(f"x{i}^2")
    X = np.column_stack(cols)
    return X, names


def fit_ols(
    predictors: list[list[float]],
    y_vec: list[float],
    quadratic: bool | Sequence[bool],
) -> Fit:
    """OLS beta = (X'X)^-1 X'y with full covariance Cov = sigma^2 (X'X)^-1.

    Uses np.linalg.lstsq for numerical stability over forming (X'X)^-1
    directly, then computes the covariance from the inverse.
    """
    y = np.asarray(y_vec, dtype=float)
    n = len(y)
    X, names = build_design_matrix(predictors, quadratic)
    p = X.shape[1]
    if n <= p + 1:
        raise ValueError(f"Need at least {p + 2} points, got {n}")

    coefs, _residuals_sum_sq, _rank, _sv = np.linalg.lstsq(X, y, rcond=None)

    residuals = y - X @ coefs
    rss = float(residuals @ residuals)
    tss = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - rss / tss if tss > 0 else float("nan")
    rmse = math.sqrt(rss / n)
    sigma2 = rss / (n - p)  # unbiased
    sigma_hat = math.sqrt(sigma2)

    # Cov(beta) = sigma^2 * (X'X)^-1.
    XtX_inv = np.linalg.inv(X.T @ X)
    cov = sigma2 * XtX_inv

    x_means = np.array([np.mean(p) for p in predictors])
    x_ranges = [(float(min(p)), float(max(p))) for p in predictors]

    return Fit(
        n=n,
        coef_names=names,
        coefs=coefs,
        cov=cov,
        r2=r2,
        rmse=rmse,
        sigma_hat=sigma_hat,
        residuals=residuals,
        x_means=x_means,
        x_ranges=x_ranges,
        y_mean=float(y.mean()),
        y_range=(float(y.min()), float(y.max())),
    )


def quintile_residuals(
    pivot_predictor: list[float],
    fit: Fit,
) -> list[dict]:
    """Group residuals by quintile of the first predictor."""
    n = len(pivot_predictor)
    order = sorted(range(n), key=lambda i: pivot_predictor[i])
    qsize = n // 5
    out = []
    for q in range(5):
        idx = order[q * qsize : (q + 1) * qsize if q < 4 else n]
        r = [float(fit.residuals[i]) for i in idx]
        xmid = pivot_predictor[idx[len(idx) // 2]]
        out.append(
            {
                "quintile": q + 1,
                "x_median": xmid,
                "mean_residual": statistics.mean(r),
                "std_residual": statistics.stdev(r) if len(r) > 1 else 0.0,
                "n": len(r),
            }
        )
    return out


def residual_percentiles(fit: Fit) -> dict[int, float]:
    """Residual at p01/p05/p25/p50/p75/p95/p99."""
    r = sorted(fit.residuals.tolist())
    n = len(r)
    return {p: r[max(0, min(n - 1, int(p / 100 * n)))] for p in (1, 5, 25, 50, 75, 95, 99)}


# Hydrologic ("monsoonal") season buckets for the residual table. Most kayak
# gauges sit in a Pacific-Northwest monsoonal regime, so bucketing residuals
# by season exposes seasonal bias the pooled diagnostics average away — e.g.,
# a fit trained on the long record can systematically under-predict during
# spring rain-on-snow if the upstream→target routing/gains in that season
# differ from the annual mean. Months are contiguous and non-overlapping;
# the water year (starts Oct 1) is split heavy-rain → light-rain →
# rain-on-snow → dry season.
SEASONS: list[tuple[str, tuple[int, ...]]] = [
    ("Heavy rain (Nov-Dec)", (11, 12)),
    ("Light rain (Jan-Feb)", (1, 2)),
    ("Rain-on-snow (Mar-Apr)", (3, 4)),
    ("Dry season (May-Oct)", (5, 6, 7, 8, 9, 10)),
]


def seasonal_residuals(
    window_keys: list[str],
    fit: Fit,
    y_values: list[float],
) -> list[dict]:
    """Bucket residuals by hydrologic season (see ``SEASONS``).

    ``window_keys[i]`` is the ``YYYY-MM-DD`` date of residual
    ``fit.residuals[i]`` and target value ``y_values[i]`` — the three share
    the ordering used to build the design matrix. Returns one row per season
    with n, mean/median bias, std, RMSE, the mean target value, and the
    percent bias (mean residual / mean target) so seasonal over/under-fit is
    directly comparable across gauges of different magnitudes.
    """
    month_to_season = {m: name for name, months in SEASONS for m in months}
    buckets: dict[str, list[int]] = {name: [] for name, _ in SEASONS}
    for i, k in enumerate(window_keys):
        buckets[month_to_season[int(k[5:7])]].append(i)
    out: list[dict] = []
    for name, _months in SEASONS:
        idx = buckets[name]
        if not idx:
            out.append({"season": name, "n": 0})
            continue
        r = [float(fit.residuals[i]) for i in idx]
        yv = [y_values[i] for i in idx]
        y_mean = statistics.mean(yv)
        out.append(
            {
                "season": name,
                "n": len(r),
                "mean_residual": statistics.mean(r),
                "median_residual": statistics.median(r),
                "std_residual": statistics.stdev(r) if len(r) > 1 else 0.0,
                "rmse": math.sqrt(sum(v * v for v in r) / len(r)),
                "y_mean": y_mean,
                "y_median": statistics.median(yv),
                "pct_bias": 100.0 * statistics.mean(r) / y_mean if y_mean else float("nan"),
            }
        )
    return out


@dataclass(frozen=True)
class CoefUncertainty:
    """Honest coefficient uncertainty + collinearity diagnostics.

    OLS SEs assume IID residuals, but daily streamflow residuals are strongly
    autocorrelated, so those SEs are optimistic. The block-bootstrap values
    here resample whole monthly calendar blocks (keeping serial correlation
    intact) and are the realistic numbers; VIFs flag the multicollinearity
    that makes individual coefficients unreliable to interpret in isolation.
    """

    boot_se: np.ndarray  # (p,) bootstrap SD of each coefficient
    boot_lo: np.ndarray  # (p,) 2.5th percentile
    boot_hi: np.ndarray  # (p,) 97.5th percentile
    vifs: list[float]  # one per linear predictor
    resid_rho1: float  # lag-1 autocorrelation of the residuals
    se_inflation: float  # median(boot_se / ols_se) over the slopes
    n_blocks: int
    n_boot: int


def coef_uncertainty(
    pts_predictors: list[list[float]],
    pts_y: list[float],
    window_keys: list[str],
    quadratic: bool | Sequence[bool],
    fit: Fit,
    n_boot: int = 1000,
    seed: int = 0,
) -> CoefUncertainty:
    """Block-bootstrap the whole fit over monthly blocks + compute VIFs.

    `window_keys[i]` is the `YYYY-MM-DD` date of row i; rows are grouped into
    `YYYY-MM` blocks so an entire month resamples together and within-month
    autocorrelation is preserved.
    """
    cols = [np.asarray(c, dtype=float) for c in pts_predictors]
    y = np.asarray(pts_y, dtype=float)
    block_id = np.array([k[:7] for k in window_keys])

    def refit(idx: np.ndarray) -> np.ndarray:
        design, _ = build_design_matrix([c[idx] for c in cols], quadratic)
        beta, *_ = np.linalg.lstsq(design, y[idx], rcond=None)
        return np.asarray(beta, dtype=float)

    reps = block_bootstrap(block_id, refit, n_boot=n_boot, seed=seed)  # (B, p)
    boot_se = reps.std(axis=0, ddof=1)
    n_lin = len(cols)
    ols_se = fit.se[1 : 1 + n_lin]
    ratio = boot_se[1 : 1 + n_lin] / np.where(ols_se > 0, ols_se, np.nan)
    return CoefUncertainty(
        boot_se=boot_se,
        boot_lo=np.percentile(reps, 2.5, axis=0),
        boot_hi=np.percentile(reps, 97.5, axis=0),
        vifs=vif(cols),
        resid_rho1=lag1_autocorr(fit.residuals),
        se_inflation=float(np.nanmedian(ratio)),
        n_blocks=len(set(block_id.tolist())),
        n_boot=n_boot,
    )


def design_row(predictor_values: list[float], quadratic: bool | Sequence[bool]) -> np.ndarray:
    """Build the design-row vector for a single x* — same column layout as
    `build_design_matrix`."""
    quad = _quad_mask(quadratic, len(predictor_values))
    cols = [1.0, *list(predictor_values)]
    cols += [v * v for v, m in zip(predictor_values, quad, strict=False) if m]
    return np.array(cols)


def predict(
    fit: Fit,
    predictor_values: list[float],
    quadratic: bool | Sequence[bool],
) -> tuple[float, float, float]:
    """Return (y_hat, se_mean_response, se_prediction) at the given x*.

    * `se_mean_response` = sqrt(X*' . Cov(beta) . X*) — uncertainty in the
      *expected* y at x*. Use this for confidence bands around the fit line.
    * `se_prediction` = sqrt(se_mean_response^2 + sigma_hat^2) — uncertainty
      in a *single new observation* at x*. Use this for prediction intervals
      around a new measurement; dominated by sigma_hat (residual scatter).
    """
    x = design_row(predictor_values, quadratic)
    y_hat = float(x @ fit.coefs)
    var_mean = float(x @ fit.cov @ x)
    se_mean = math.sqrt(max(0.0, var_mean))
    se_pred = math.sqrt(max(0.0, var_mean + fit.sigma_hat * fit.sigma_hat))
    return y_hat, se_mean, se_pred


def stability_windows(
    target: dict[str, float],
    predictor_dicts: list[dict[str, float]],
    end: str,
    starts: list[str],
    quadratic: bool | Sequence[bool],
) -> list[tuple[str, Fit | None]]:
    """Re-fit at multiple start dates so coefficient drift is visible."""
    keys = sorted(set(target) & set[str].intersection(*(set(d) for d in predictor_dicts)))
    out: list[tuple[str, Fit | None]] = []
    for start in starts:
        window = [d for d in keys if start <= d <= end]
        if len(window) < 10:
            out.append((start, None))
            continue
        preds = [[d[k] for k in window] for d in predictor_dicts]
        ys = [target[k] for k in window]
        try:
            out.append((start, fit_ols(preds, ys, quadratic)))
        except (ValueError, np.linalg.LinAlgError):
            out.append((start, None))
    return out


def _load_leadlag(slug: str | None, out: Path | None) -> dict | None:
    """Load a gauge_lead_lag.py JSON summary (a sibling of ``out``) by slug."""
    if not slug or not out:
        return None
    path = out.parent / f"{slug}.json"
    if not path.exists():
        print(f"--leadlag: {path} not found; skipping section", file=sys.stderr)
        return None
    loaded: dict = _json.loads(path.read_text())
    return loaded


def _render_leadlag_section(leadlag: dict) -> list[str]:
    """Render the 'Sub-daily lead/lag' section from a gauge_lead_lag.py summary.

    Embeds the travel-time lags + full/deployable gain so the daily report is
    self-contained on timing, with a link to the full companion analysis.
    """
    out: list[str] = []
    a = out.append
    slug = leadlag["slug"]
    a("## Sub-daily lead/lag\n")
    a(
        f"Inter-gauge travel-time structure from USGS unit values "
        f"({leadlag['grid_minutes']}-min grid, {leadlag['n_points']:,} points); full "
        f"analysis in [`{slug}.md`](./{slug}.md). The daily coefficients above are "
        "applied in production to *instantaneous* readings, so these lags are the timing "
        "error a correction would address. **+τ** = upstream (a past read, deployable in "
        "real time); **-τ** = downstream (a future read — non-causal look-ahead).\n"
    )
    a("| Predictor | applied τ (h) | Δ-corr | direction |")
    a("|---|---|---|---|")
    for lg in leadlag["lags"]:
        if not lg["identifiable"]:
            direction = "held (not identifiable)"
        elif lg["applied_lag_h"] > 0:
            direction = "upstream — deployable"
        elif lg["applied_lag_h"] < 0:
            direction = "downstream — look-ahead"
        else:
            direction = "co-located"
        corr = f"{lg['corr']:.3f}" if lg["corr"] is not None else "—"
        a(f"| {lg['label']} `{lg['site']}` | {lg['applied_lag_h']:+.1f} | {corr} | {direction} |")
    a("")
    if leadlag.get("rmse_valid") and "full" in leadlag:
        f, d = leadlag["full"], leadlag["deploy"]
        rec = (
            "a deployable gain worth considering"
            if leadlag["verdict"] == "usable"
            else "keep using contemporaneous readings"
        )
        a(
            f"**Full** alignment (incl. downstream → future): {f['gain_pct']:+.1f}% RMSE, "
            f"95% CI [{f['ci'][0]:+.2f}, {f['ci'][1]:+.2f}] cfs "
            f"({'resolved' if f['resolved'] else 'CI through 0'}). **Deployable** (causal, "
            f"upstream-only): {d['gain_pct']:+.1f}%, [{d['ci'][0]:+.2f}, {d['ci'][1]:+.2f}] "
            f"cfs ({'resolved' if d['resolved'] else 'CI through 0'}). "
            f"**Verdict: {leadlag['verdict_label']}** — {rec}.\n"
        )
    else:
        a(
            f"Lags shown for timing only; the RMSE comparison was omitted ({leadlag['verdict_label']}).\n"
        )
    return out


def render_markdown(  # noqa: C901 — assembly of many table sections; refactor not worth it
    *,
    name: str,
    predictor_sites: list[str],
    target_site: str,
    window_start: str,
    window_end: str,
    quadratic: bool | Sequence[bool],
    target_data: dict[str, float],
    predictor_data: list[dict[str, float]],
    overlap_keys: list[str],
    window_keys: list[str],
    pts_predictors: list[list[float]],
    pts_y: list[float],
    fit: Fit,
    coef_unc: CoefUncertainty,
    stab: list[tuple[str, Fit | None]],
    calc_handles: list[str],
    leadlag: dict | None = None,
    deploy_note: str | None = None,
) -> str:
    """Build the markdown analysis report."""
    pct = residual_percentiles(fit)
    quins = quintile_residuals(pts_predictors[0], fit)
    quad_mask = _quad_mask(quadratic, len(predictor_sites))

    # Map coef_names → reference label for the table.
    label_for: dict[str, str] = {"intercept": "intercept"}
    for i, h in enumerate(calc_handles, start=1):
        label_for[f"x{i}"] = f"{h} (predictor {i}: {predictor_sites[i - 1]})"
        if quad_mask[i - 1]:
            label_for[f"x{i}^2"] = f"({predictor_sites[i - 1]})²"

    # SQL stub
    intercept = fit.coefs[0]
    sql_terms = []
    for i, h in enumerate(calc_handles, start=1):
        coef = fit.coefs[1 + (i - 1)]
        sql_terms.append(f"{coef:.6g} * {h}::flow")
    sq_idx = 1 + len(calc_handles)
    for i, h in enumerate(calc_handles, start=1):
        if not quad_mask[i - 1]:
            continue
        coef = fit.coefs[sq_idx]
        sq_idx += 1
        sql_terms.append(f"{coef:.6g} * {h}::flow * {h}::flow")
    intercept_str = f"{intercept:+.4g}"
    inner = " + ".join(sql_terms) + f" {intercept_str}"
    expr_sql = f"round(greatest(0, {inner}))"
    time_expr = " ".join(f"{h}::flow" for h in calc_handles)

    lines: list[str] = []
    L = lines.append

    family = "linear"
    if len(predictor_sites) > 1:
        family = f"multi-{family}"
    if all(quad_mask):
        family += "+quadratic"
    elif any(quad_mask):
        squared = [s for s, m in zip(predictor_sites, quad_mask, strict=False) if m]
        family += f"+quadratic({','.join(squared)})"

    L(f"# {family.title()} regression: USGS {target_site} from {', '.join(predictor_sites)}\n")
    L(
        f"**Goal**: estimate USGS `{target_site}` from "
        f"{', '.join(f'`{s}`' for s in predictor_sites)} "
        f"so a downstream `calc_expression` can replace the target gauge.\n"
    )
    # GitHub renders the SVG inline when viewing the .md; the kayak build
    # also pre-renders this .md → HTML and serves the same SVG sibling at
    # /static/regression/<slug>.svg.
    L(f"![Residuals scatter](./{name}.svg)\n")
    cmd_parts = (
        ["python3 scripts/regression/gauge_pair_linear.py"]
        + [f"--predictor {s}" for s in predictor_sites]
        + [
            f"--target {target_site}",
            f"--start {window_start}",
            f"--end {window_end}",
            f"--name {name}",
        ]
    )
    # Custom calc handles are part of the output (the calc_expression row), so
    # the reproduce command must carry them or a regen would silently emit
    # default p1::<site> handles. Defaults are omitted — they self-reproduce.
    default_handles = [f"p{i}::{s}" for i, s in enumerate(predictor_sites, start=1)]
    if calc_handles != default_handles:
        cmd_parts += [f"--calc-handle {h}" for h in calc_handles]
    if deploy_note:
        cmd_parts.append(f"--deploy-note {shlex.quote(deploy_note)}")
    if all(quad_mask):
        cmd_parts.append("--quadratic")
    else:
        cmd_parts += [
            f"--quadratic-for {s}" for s, m in zip(predictor_sites, quad_mask, strict=False) if m
        ]
    cmd = " \\\n    ".join(cmd_parts)
    L(f"Generated by:\n\n```bash\n{cmd}\n```\n")

    L("## Data\n")
    L("All series are USGS daily-mean flow (`parameterCd=00060`, `statCd=00003`).\n")
    L("| Gauge | Period of record | Daily means |")
    L("|---|---|---|")
    L(
        f"| `{target_site}` (target) | {min(target_data)} → **{max(target_data)}** | {len(target_data)} |"
    )
    for site, d in zip(predictor_sites, predictor_data, strict=False):
        L(f"| `{site}` (predictor) | {min(d)} → {max(d)} | {len(d)} |")
    L(f"| **Overlap (full)** | {overlap_keys[0]} → {overlap_keys[-1]} | **{len(overlap_keys)}** |")
    L("")
    L("Note: USGS records can be **non-contiguous** (instrumentation outages).")
    L("The chosen window is selected for *data points*, not calendar span.\n")

    L("## Chosen fit\n")
    L(
        f"Window: **{window_start} → {window_end}**, n = **{fit.n}** "
        f"daily means (~{fit.n / 365.25:.1f} years of data).\n"
    )

    L("### Coefficients (with honest, autocorrelation-aware uncertainty)\n")
    L(
        "Daily streamflow residuals are strongly autocorrelated (lag-1 "
        f"**{coef_unc.resid_rho1:.2f}** here), which violates the IID assumption "
        "behind the OLS standard errors — so **SE (OLS)** is optimistic. **SE "
        f"(block-boot)** resamples whole monthly blocks ({coef_unc.n_blocks} "
        f"months, B={coef_unc.n_boot}), preserving the serial correlation; it is "
        f"the realistic figure and runs about **{coef_unc.se_inflation:.1f}x** the "
        "OLS SE. The **95% CI** below is the block-bootstrap percentile interval. "
        "**VIF** is the variance-inflation factor (collinearity with the other "
        "predictors); VIF > 10 means the individual coefficient is poorly "
        "determined and should not be read as a physical sensitivity.\n"
    )
    vif_for = {f"x{j}": coef_unc.vifs[j - 1] for j in range(1, len(coef_unc.vifs) + 1)}
    L("| Term | Estimate | SE (OLS) | SE (block-boot) | 95% CI (block-boot) | VIF |")
    L("|---|---|---|---|---|---|")
    for i, (name_, est, se) in enumerate(zip(fit.coef_names, fit.coefs, fit.se, strict=False)):
        label = label_for.get(name_, name_)
        bse = coef_unc.boot_se[i]
        blo, bhi = coef_unc.boot_lo[i], coef_unc.boot_hi[i]
        vstr = f"{vif_for[name_]:.1f}" if name_ in vif_for else "—"
        L(f"| {label} | {est:+.6g} | {se:.4g} | {bse:.4g} | [{blo:+.4g}, {bhi:+.4g}] | {vstr} |")
    L("")
    L(
        f"r² = **{fit.r2:.4f}**, RMSE = **{fit.rmse:.2f} cfs** "
        f"(sigma_hat = {fit.sigma_hat:.2f} cfs unbiased).\n"
    )
    L("Predictor / target summary:\n")
    L("| Series | Mean | Range |")
    L("|---|---|---|")
    L(
        f"| target `{target_site}` | {fit.y_mean:.2f} | [{fit.y_range[0]:.0f}, {fit.y_range[1]:.0f}] |"
    )
    for site, mean_, (lo, hi) in zip(predictor_sites, fit.x_means, fit.x_ranges, strict=False):
        L(f"| predictor `{site}` | {mean_:.2f} | [{lo:.0f}, {hi:.0f}] |")
    L("")

    L("### Parameter covariance\n")
    L("Full variance-covariance matrix (rows/cols in `coef_names` order):\n")
    L("```")
    header = "             " + "  ".join(f"{n:>12}" for n in fit.coef_names)
    L(header)
    for i, n in enumerate(fit.coef_names):
        row = "  ".join(f"{fit.cov[i, j]:+.4e}" for j in range(len(fit.coef_names)))
        L(f"{n:>12}  {row}")
    L("```\n")
    L("Correlation matrix:\n")
    L("```")
    L("             " + "  ".join(f"{n:>10}" for n in fit.coef_names))
    corr = fit.corr
    for i, n in enumerate(fit.coef_names):
        row = "  ".join(f"{corr[i, j]:+.4f}    " for j in range(len(fit.coef_names)))
        L(f"{n:>12}  {row}")
    L("```\n")
    L(
        "**Caveat 1 (autocorrelation)**: this is the **OLS** covariance, which "
        "assumes IID residuals; with lag-1 residual autocorrelation "
        f"**{coef_unc.resid_rho1:.2f}** it understates the parameter SE by roughly "
        f"**{coef_unc.se_inflation:.1f}x**. Use the block-bootstrap SEs/CIs in the "
        "coefficients table for inference, not these (monthly blocks; longer "
        "blocks would only widen the intervals, so they are conservative for the "
        "most autocorrelated fits).\n"
    )
    L(
        "**Caveat 2 (prediction vs parameter)**: even with correct parameter "
        "SEs, a single-day prediction at new `x` is dominated by the residual "
        f"scatter `sigma_hat` (about {fit.sigma_hat:.0f} cfs at 1-sigma here), "
        "not by parameter uncertainty. `sigma_hat` is a valid *marginal* "
        "description of single-day error (autocorrelation barely biases it); "
        "what autocorrelation breaks is treating the n days as n independent "
        "observations.\n"
    )

    L("## Window stability\n")
    L(f"Re-fit at multiple start dates (endpoint fixed at `{window_end}`):\n")
    if not any(quad_mask) and len(predictor_sites) == 1:
        L("| Window start | n | data yr | slope | intercept | r² | RMSE | SE(slope) | SE(int) |")
        L("|---|---|---|---|---|---|---|---|---|")
        for start, f in stab:
            if f is None:
                L(f"| {start} | — | — | — | — | — | — | — | — |")
                continue
            L(
                f"| {start} | {f.n} | {f.n / 365.25:.1f} | {f.coefs[1]:.4f} | "
                f"{f.coefs[0]:+.2f} | {f.r2:.4f} | {f.rmse:.1f} | "
                f"{f.se[1]:.4f} | {f.se[0]:.2f} |"
            )
    else:
        # Compact: just show r²/RMSE/n for multi-predictor / quadratic.
        L("| Window start | n | data yr | r² | RMSE |")
        L("|---|---|---|---|---|")
        for start, f in stab:
            if f is None:
                L(f"| {start} | — | — | — | — |")
                continue
            L(f"| {start} | {f.n} | {f.n / 365.25:.1f} | {f.r2:.4f} | {f.rmse:.1f} |")
        L(
            "\n(Multi-predictor coefficients in the stability table would be "
            "wide; per-window coefficient drift can be inspected by re-running "
            "the script with a different `--start`.)"
        )
    L("")

    L("## Residual diagnostics\n")
    L("**Percentile distribution** (residual = y - y_hat, cfs):\n")
    L("| p01 | p05 | p25 | p50 | p75 | p95 | p99 |")
    L("|---|---|---|---|---|---|---|")
    L("| " + " | ".join(f"{pct[p]:+.1f}" for p in (1, 5, 25, 50, 75, 95, 99)) + " |")
    L("")
    L(f"**By predictor-1 quintile** (Q1 = lowest values of `{predictor_sites[0]}`):\n")
    L("| Quintile | x median | mean residual | std residual | n |")
    L("|---|---|---|---|---|")
    for q in quins:
        L(
            f"| Q{q['quintile']} | {q['x_median']:.0f} | "
            f"{q['mean_residual']:+.1f} | {q['std_residual']:.1f} | {q['n']} |"
        )
    L("")

    L("### By hydrologic season\n")
    L(
        "Residuals bucketed by monsoonal season (most kayak gauges sit in a "
        "PNW monsoonal regime). **Mean / median flow** give each season's "
        "target-flow magnitude. **Bias** is the mean residual (y - y_hat); a "
        "non-zero bias means the pooled fit systematically over- (negative) "
        "or under-predicts (positive) in that season. **% of flow** "
        "normalizes the bias by the season's mean flow so it's comparable "
        "across gauges. The remaining columns (median residual, std, RMSE) "
        "are residual statistics in cfs.\n"
    )
    seas = seasonal_residuals(window_keys, fit, pts_y)
    L(
        "| Season | n | mean flow | median flow | bias (cfs) | % of flow | "
        "median resid | std | RMSE |"
    )
    L("|---|---|---|---|---|---|---|---|---|")
    for s in seas:
        if s["n"] == 0:
            L(f"| {s['season']} | 0 | — | — | — | — | — | — | — |")
            continue
        L(
            f"| {s['season']} | {s['n']} | {s['y_mean']:.0f} | {s['y_median']:.0f} | "
            f"{s['mean_residual']:+.1f} | {s['pct_bias']:+.1f}% | "
            f"{s['median_residual']:+.1f} | {s['std_residual']:.1f} | {s['rmse']:.1f} |"
        )
    L("")
    L(
        "A season whose bias is large relative to `sigma_hat` (the pooled "
        "1-sigma residual scatter) is a candidate for a season-specific "
        "intercept or a separate seasonal fit; a season with elevated `std`/"
        "`RMSE` but near-zero bias is just noisier (e.g., flashy storm "
        "response), not mis-calibrated.\n"
    )

    if leadlag is not None:
        lines.extend(_render_leadlag_section(leadlag))

    L("## Predictions at example x values\n")
    L(
        "For each row, `y_hat` is the fitted value and the two CIs are 95% "
        "two-sided bands. The **mean-response CI** is the uncertainty in "
        "`E[y | x]` (use for plotting the fit line's confidence band). The "
        "**prediction CI** is for a *single new observation* — bounded "
        "below by `sigma_hat` regardless of how precisely the parameters "
        "are estimated.\n"
    )
    # Pick example x* at p05/p25/p50/p75/p95 of the FIRST predictor.
    # For multi-predictor, hold the other predictors at their means
    # (visualizes the marginal effect of predictor 1).
    sorted_p1 = sorted(pts_predictors[0])
    n_p = len(sorted_p1)

    def at(p: float) -> float:
        return sorted_p1[max(0, min(n_p - 1, int(p * n_p)))]

    example_x1 = [
        ("p05 (low)", at(0.05)),
        ("p25", at(0.25)),
        ("p50 (median)", at(0.50)),
        ("p75", at(0.75)),
        ("p95 (high)", at(0.95)),
    ]
    header_cols = " | ".join(f"x ({site})" for site in predictor_sites)
    L(f"| pred-1 position | {header_cols} | y_hat | 95% CI (mean resp.) | 95% CI (single obs.) |")
    L("|---|" + "|".join("---" for _ in predictor_sites) + "|---|---|---|")
    for label, x1 in example_x1:
        # Hold predictors 2..N at their means (marginal view of predictor 1).
        xs_all = [x1, *fit.x_means[1:].tolist()]
        y_hat, se_mean, se_pred = predict(fit, xs_all, quad_mask)
        lo_mean = y_hat - 1.96 * se_mean
        hi_mean = y_hat + 1.96 * se_mean
        lo_pred = y_hat - 1.96 * se_pred
        hi_pred = y_hat + 1.96 * se_pred
        x_str = " | ".join(f"{v:.0f}" for v in xs_all)
        L(
            f"| {label} | {x_str} | {y_hat:.1f} | "
            f"[{lo_mean:.1f}, {hi_mean:.1f}] (±{1.96 * se_mean:.1f}) | "
            f"[{lo_pred:.1f}, {hi_pred:.1f}] (±{1.96 * se_pred:.1f}) |"
        )
    L("")
    L("### Computing a CI at any other x*\n")
    L(
        "All the information needed to compute prediction CIs at any new "
        "predictor value is in this document. With the design row "
        "`X* = [1, x1*, x2*, ...]` — plus a squared column for each "
        "predictor fitted quadratically, in predictor order — matching the "
        "column order in the covariance matrix above:\n"
    )
    L("```")
    L("y_hat = X* . coefs")
    L("Var(mean response) = X* . Cov(beta) . X*'")
    L("Var(single observation) = Var(mean response) + sigma_hat^2")
    L("SE = sqrt(Var)")
    L("95% CI = y_hat +/- 1.96 * SE     (n >> 30, large-sample z; use t_{n-p} for small n)")
    L("```\n")
    if len(predictor_sites) == 1 and not any(quad_mask):
        # Single-predictor linear: provide the closed-form formula too.
        L("For this single-predictor linear fit, the equivalent closed form is:\n")
        L("```")
        L("Var(mean response at x*) = sigma_hat^2 * (1/n + (x* - mean_x)^2 / Sxx)")
        L(
            "                         where mean_x = "
            f"{fit.x_means[0]:.4f}, "
            f"sigma_hat = {fit.sigma_hat:.4f},"
        )
        L(
            f"                         n = {fit.n}, "
            f"Sxx = sigma_hat^2 / SE(slope)^2 = {fit.sigma_hat**2 / fit.se[1] ** 2:.4e}"
        )
        L("```\n")

    L("## `calc_expression` row\n")
    L(
        "`calc_expression` rows are **metadata**: add a row to "
        "`calc_expression.csv` in the `kayak_data` repo (stable `id` from "
        "`id_counters.csv`, `provenance_slug` = this report's slug) and let "
        "`levels sync-metadata` apply it on deploy. Do **not** put this in a "
        "migration — a new migration may not write a metadata table "
        "(`tests/test_scripts/test_migrations_schema_only.py`). The handles "
        f"({', '.join(f'`{h}`' for h in calc_handles)}) follow the "
        "`prefix::gauge_name` convention enforced by "
        "`kayak.cli.calculator._resolve_refs`. Column values:\n"
    )
    note = (
        f"{family} regression fit. n={fit.n} daily means, "
        f"window {window_start}..{window_end}, r2={fit.r2:.4f}, "
        f"RMSE={fit.rmse:.1f} cfs. See docs/regression/{name}.md."
    )
    L("```")
    L("data_type:       flow")
    L(f"expression:      {expr_sql}")
    L(f"time_expression: {time_expr}")
    L(f"note:            {note}")
    L(f"provenance_slug: {name}")
    L("```\n")
    if deploy_note:
        L(
            f"⚠️ **Deployment note — the deployed expression differs from this "
            f"fit**: {deploy_note} Do not copy the expression above verbatim; "
            "apply the stated composition first.\n"
        )
    L(
        "Flesh out `note` before committing — the strongest existing rows "
        "also record window stability, rejected predictors, and any "
        "drainage-area scaling (see `calc_expression.csv` for examples).\n"
    )

    L("## Future\n")
    L(
        "- **Piecewise-linear fit by predictor-1 quintile.** If the residual "
        "table above shows systematic mean drift across quintiles (e.g., "
        "consistently under-estimating at low flow and over-estimating at "
        "high flow), splitting the predictor range into 2-3 regimes and "
        "fitting one linear model per regime can halve RMSE without adding "
        "free parameters beyond what `calc_expression` already supports via "
        "`greatest(low_estimate, high_estimate)` or "
        "`if(x < threshold, ..., ...)`-style composition. Worth trying when "
        "RMSE > ~10% of the mean target value."
    )
    L(
        "- **Re-running** when the active predictor's rating curve drifts. "
        "USGS occasionally updates stage-discharge ratings; the `Reproduce` "
        "snippet above re-pulls the full period of record on demand."
    )
    if leadlag is None:
        L(
            "- **Sub-daily lead/lag.** This fit is on daily means, but the "
            "`calc_expression` applies its coefficients to the *latest instantaneous* "
            "predictor readings — so inter-gauge travel time (1-12 h) becomes a timing "
            "error the daily fit never sees. `gauge_lead_lag.py` (same directory) "
            "quantifies that error from USGS unit values; worth a look when predictors "
            "are many river-miles from the target. (Run it to embed a summary here via "
            "`--leadlag`.)"
        )

    return "\n".join(lines) + "\n"


def _nice_axis(data_min: float, data_max: float) -> tuple[float, float, float]:
    """Compute (lo, hi, step) for round tick labels — Python port of
    `php/includes/svg_plot.php::nice_axis`.

    Targets 4-8 ticks; falls back to the candidate closest to 5 ticks.
    """
    rng = data_max - data_min
    if rng < 1e-9:
        rng = 1.0
    mag = 10 ** math.floor(math.log10(rng))
    candidates = [5 * mag, 2 * mag, 1 * mag, 0.5 * mag, 0.2 * mag, 0.1 * mag]
    for step in candidates:
        lo = math.floor(data_min / step) * step
        hi = math.ceil(data_max / step) * step
        n_ticks = round((hi - lo) / step)
        if 4 <= n_ticks <= 8:
            return lo, hi, step
    step = candidates[0]
    for s in candidates:
        lo = math.floor(data_min / s) * s
        hi = math.ceil(data_max / s) * s
        n = round((hi - lo) / s)
        if 3 <= n <= 10:
            step = s
            break
    lo = math.floor(data_min / step) * step
    hi = math.ceil(data_max / step) * step
    return lo, hi, step


def _format_tick(v: float, step: float) -> str:
    """Pick a decimal precision that's "just enough" for the step size."""
    if step >= 100:
        return f"{v:.0f}"
    if step >= 1:
        return f"{v:.0f}" if abs(v - round(v)) < 1e-6 else f"{v:.1f}"
    decimals = max(0, -math.floor(math.log10(step)))
    return f"{v:.{decimals}f}"


def _lowess_sigma_band(
    *,
    x_values: list[float],
    residuals: list[float],
    x_min: float,
    x_max: float,
    n_eval: int = 100,
    frac: float = 0.3,
) -> list[tuple[float, float]]:
    """Tricube-weighted local estimate of 1.96 * sigma(x).

    Estimates sigma(x) by smoothing the squared residuals: for each
    evaluation x*, take the k = round(frac * n) nearest points by |x_i - x*|,
    weight each by the tricube of normalized distance, and return
    sqrt(weighted mean of r_i^2). Variance is the right quantity to
    smooth because E[r^2] = sigma^2 at each x (under the bias-zero
    assumption that holds for OLS at the fitted x's), so the smoother
    is locally unbiased.

    Returns a list of (x_eval, 1.96 * sigma_eval) pairs for plotting.
    """
    n = len(x_values)
    x_arr = np.asarray(x_values, dtype=float)
    r2_arr = np.asarray(residuals, dtype=float) ** 2
    k = max(round(frac * n), 5)
    k = min(k, n)

    out: list[tuple[float, float]] = []
    for i in range(n_eval):
        x_star = x_min + (x_max - x_min) * i / max(1, n_eval - 1)
        d = np.abs(x_arr - x_star)
        # k nearest neighbors by x.
        nn = np.argpartition(d, k - 1)[:k]
        d_nn = d[nn]
        d_max = d_nn.max() if d_nn.max() > 0 else 1.0
        # Tricube weights: (1 - (d/d_max)^3)^3, clamped to >= 0.
        w = np.clip(1.0 - (d_nn / d_max) ** 3, 0.0, 1.0) ** 3
        w_sum = float(w.sum())
        if w_sum <= 0:
            sigma_at = float(np.sqrt(r2_arr[nn].mean()))
        else:
            var_at = float((w * r2_arr[nn]).sum() / w_sum)
            sigma_at = math.sqrt(max(var_at, 0.0))
        out.append((x_star, 1.96 * sigma_at))
    return out


def _render_residuals_svg(
    *,
    slug: str,
    fit: Fit,
    pivot_predictor: list[float],
    target_site: str,
    predictor_site: str,
    max_points: int = 1500,
) -> str:
    """Residuals scatter SVG: predictor flow on x, residual (y - y_hat) on y,
    lowess-smoothed +/-1.96 * sigma_hat(x) band, y=0 line, ~max_points
    subsampled.

    The band is a lowess estimate of the local residual standard
    deviation (continuous, not stepped) because daily-flow residuals
    are heteroscedastic -- for the Rogue fit the local std runs from
    about 55 cfs at low flow to 180 cfs at high flow. A constant
    pooled band would under-state scatter at high flow and over-state
    it at low flow.

    Standalone -- no CSS or JS dependencies; serves as `<img src=...>`.
    """
    n_points = len(pivot_predictor)

    # Cap the chart's x-axis at the 99th percentile of predictor flow so
    # the lowess band reflects the well-sampled region. Rare extreme
    # flood points (typically once-a-year events) get cropped — without
    # this, the band balloons at the tail because the local sigma
    # estimator picks up a handful of huge storm residuals with little
    # supporting data.
    x_data_min = min(pivot_predictor)
    x_data_max_full = max(pivot_predictor)
    if n_points >= 100:
        sorted_x = sorted(pivot_predictor)
        x_data_max = sorted_x[int(0.99 * (len(sorted_x) - 1))]
    else:
        x_data_max = x_data_max_full

    rng = random.Random(slug)  # deterministic per-slug subsample
    # Subsample only points within the displayed x-range.
    in_range = [i for i, x in enumerate(pivot_predictor) if x <= x_data_max]
    if len(in_range) > max_points:
        sample_idx = rng.sample(in_range, max_points)
    else:
        sample_idx = in_range

    xs = [pivot_predictor[i] for i in sample_idx]
    ys = [float(fit.residuals[i]) for i in sample_idx]

    # Lowess-smoothed sigma(x): tricube-weighted local std of residuals.
    # Returns one (x_eval, half_band) pair per grid point.
    if n_points >= 20:
        band_curve = _lowess_sigma_band(
            x_values=pivot_predictor,
            residuals=[float(r) for r in fit.residuals],
            x_min=x_data_min,
            x_max=x_data_max,
            n_eval=100,
            frac=0.3,
        )
    else:
        # Fallback for tiny synthetic fits (test suite): flat band at pooled sigma.
        band_curve = [
            (x_data_min, 1.96 * fit.sigma_hat),
            (x_data_max, 1.96 * fit.sigma_hat),
        ]
    band_max = max(half for _, half in band_curve)

    # y-axis range over residuals INSIDE the displayed x-window
    # (extreme tail events live outside the chart now and shouldn't
    # stretch the y-axis vertically).
    visible_residuals = [float(fit.residuals[i]) for i in sample_idx] or [0.0]
    r_min_visible = min(visible_residuals)
    r_max_visible = max(visible_residuals)
    y_data_min = min(r_min_visible, -band_max)
    y_data_max = max(r_max_visible, band_max)

    x_lo, x_hi, x_step = _nice_axis(x_data_min, x_data_max)
    y_lo, y_hi, y_step = _nice_axis(y_data_min, y_data_max)

    # Layout: 600 x 400, with margins for titles and axis labels.
    w_total, h_total = 600, 400
    ml, mr, mt, mb = 70, 20, 50, 50
    pw = w_total - ml - mr
    ph = h_total - mt - mb

    def x_to_px(x: float) -> float:
        return ml + (x - x_lo) / (x_hi - x_lo) * pw

    def y_to_px(y: float) -> float:
        return mt + (y_hi - y) / (y_hi - y_lo) * ph

    title = f"Residuals: USGS {target_site} vs USGS {predictor_site} (n={fit.n}, r²={fit.r2:.4f})"
    cropped = x_data_max < x_data_max_full
    subtitle = (
        f"x-axis capped at p99 of predictor flow ({x_data_max:.0f} cfs); "
        f"{n_points - len(in_range)} extreme points off-chart"
        if cropped
        else ""
    )

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w_total}" height="{h_total}" '
        f'viewBox="0 0 {w_total} {h_total}" '
        'font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif" '
        'font-size="12">'
    )
    parts.append(f"<title>{title}</title>")

    # White background.
    parts.append(f'<rect x="0" y="0" width="{w_total}" height="{h_total}" fill="#fff"/>')

    # Title.
    parts.append(
        f'<text x="{w_total / 2}" y="22" text-anchor="middle" '
        f'font-size="14" font-weight="600">{title}</text>'
    )
    if subtitle:
        parts.append(
            f'<text x="{w_total / 2}" y="38" text-anchor="middle" '
            f'font-size="10" fill="#666">{subtitle}</text>'
        )

    # 95% confidence band: lowess-smoothed sigma(x), drawn as a single
    # closed polygon. Upper boundary traverses x_lo -> x_hi, lower
    # boundary returns x_hi -> x_lo, then auto-close. Drawn first so
    # the scatter points render on top.
    upper_points = [(x_to_px(x), y_to_px(half)) for x, half in band_curve]
    lower_points = [(x_to_px(x), y_to_px(-half)) for x, half in band_curve]
    path_cmds = [f"M {upper_points[0][0]:.1f} {upper_points[0][1]:.1f}"]
    path_cmds.extend(f"L {px:.1f} {py:.1f}" for px, py in upper_points[1:])
    path_cmds.extend(f"L {px:.1f} {py:.1f}" for px, py in reversed(lower_points))
    path_cmds.append("Z")
    parts.append(
        f'<path d="{" ".join(path_cmds)}" fill="#1b5591" fill-opacity="0.12" stroke="none"/>'
    )
    # Annotate the widest band on the right.
    top_px_for_max = y_to_px(band_max)
    parts.append(
        f'<text x="{ml + pw - 4}" y="{top_px_for_max - 4:.1f}" text-anchor="end" '
        f'fill="#1b5591" font-size="10">'
        f"±1.96 &#963;&#770;(x) lowess-smoothed (max ±{band_max:.0f} cfs)</text>"
    )

    # Plot frame.
    parts.append(
        f'<rect x="{ml}" y="{mt}" width="{pw}" height="{ph}" '
        'fill="none" stroke="#999" stroke-width="1"/>'
    )

    # X-axis ticks.
    n_xticks = max(1, round((x_hi - x_lo) / x_step))
    for i in range(n_xticks + 1):
        v = x_lo + i * x_step
        px = x_to_px(v)
        parts.append(
            f'<line x1="{px:.1f}" y1="{mt + ph}" x2="{px:.1f}" y2="{mt + ph + 5}" stroke="#999"/>'
        )
        parts.append(
            f'<text x="{px:.1f}" y="{mt + ph + 18}" text-anchor="middle">'
            f"{_format_tick(v, x_step)}</text>"
        )

    # Y-axis ticks.
    n_yticks = max(1, round((y_hi - y_lo) / y_step))
    for i in range(n_yticks + 1):
        v = y_lo + i * y_step
        py = y_to_px(v)
        parts.append(f'<line x1="{ml - 5}" y1="{py:.1f}" x2="{ml}" y2="{py:.1f}" stroke="#999"/>')
        parts.append(
            f'<text x="{ml - 8}" y="{py + 4:.1f}" text-anchor="end">'
            f"{_format_tick(v, y_step)}</text>"
        )

    # y=0 reference line.
    zero_px = y_to_px(0.0)
    parts.append(
        f'<line x1="{ml}" y1="{zero_px:.1f}" x2="{ml + pw}" y2="{zero_px:.1f}" '
        'stroke="#333" stroke-width="1" stroke-dasharray="2,2"/>'
    )

    # Scatter points.
    for x, y in zip(xs, ys, strict=True):
        cx = x_to_px(x)
        cy = y_to_px(y)
        parts.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="1.6" fill="#1b5591" fill-opacity="0.45"/>'
        )

    # Axis labels.
    parts.append(
        f'<text x="{ml + pw / 2}" y="{h_total - 12}" text-anchor="middle" '
        f'font-size="12">USGS {predictor_site} flow (cfs)</text>'
    )
    parts.append(
        f'<text x="{16}" y="{mt + ph / 2}" text-anchor="middle" '
        f'font-size="12" transform="rotate(-90 16 {mt + ph / 2})">'
        f"USGS {target_site} residual (cfs)</text>"
    )

    parts.append("</svg>")
    return "".join(parts) + "\n"


def _render_fit_json(
    *,
    slug: str,
    fit: Fit,
    target_site: str,
    predictor_sites: list[str],
    quadratic: bool | Sequence[bool],
    window_start: str,
    window_end: str,
) -> str:
    """Structured fit summary consumed by PHP gauge_detail.php.

    Schema is uniform across single/multi/quadratic — coefs[] is the
    iteration target for the fact-box, cov stays as a matrix so future
    code can compute prediction CIs at a new x* without re-fetching data.
    `quadratic` stays a bool (any squared term present) for backward
    compatibility; `quadratic_sites` lists which predictors are squared.
    """
    coefs_out: list[dict[str, object]] = []
    for raw_name, est, se in zip(fit.coef_names, fit.coefs, fit.se, strict=False):
        if raw_name == "intercept":
            name = "intercept"
        elif raw_name.startswith("x") and "^" not in raw_name:
            # x1, x2 → label with the matching predictor site_no.
            idx = int(raw_name[1:]) - 1
            site = predictor_sites[idx] if 0 <= idx < len(predictor_sites) else raw_name
            name = f"{raw_name} ({site})"
        elif raw_name.endswith("^2"):
            idx = int(raw_name[1:-2]) - 1
            site = predictor_sites[idx] if 0 <= idx < len(predictor_sites) else raw_name
            name = f"{raw_name} ({site})"
        else:
            name = raw_name
        coefs_out.append({"name": name, "value": float(est), "se": float(se)})

    quad_mask = _quad_mask(quadratic, len(predictor_sites))
    payload = {
        "slug": slug,
        "target": target_site,
        "predictors": predictor_sites,
        "quadratic": any(quad_mask),
        "quadratic_sites": [s for s, m in zip(predictor_sites, quad_mask, strict=False) if m],
        "n": fit.n,
        "window": [window_start, window_end],
        "coefs": coefs_out,
        "cov": [[float(v) for v in row] for row in fit.cov],
        "r2": float(fit.r2),
        "rmse": float(fit.rmse),
        "sigma_hat": float(fit.sigma_hat),
        "x_mean": [float(v) for v in fit.x_means],
        "y_mean": float(fit.y_mean),
        "x_range": [[float(lo), float(hi)] for lo, hi in fit.x_ranges],
        "y_range": [float(fit.y_range[0]), float(fit.y_range[1])],
    }
    return _json.dumps(payload, indent=2) + "\n"


def _default_stability_starts(start: str, end: str, earliest_overlap: str) -> list[str]:
    """Default start dates for the stability sweep: {start-5y, start,
    start+5y, start+10y, start+15y, earliest-overlap, 1990-01-01}, deduped,
    sorted, and capped at the window end (so the later offsets drop out of
    short windows)."""
    anchor = datetime.fromisoformat(start)
    defaults: list[str] = []
    for years_back in (-5, 0, 5, 10, 15):
        d_dt = anchor + timedelta(days=365 * years_back)
        defaults.append(d_dt.strftime("%Y-%m-%d"))
    defaults.append(earliest_overlap)
    defaults.append("1990-01-01")
    return [d_str for d_str in sorted(set(defaults)) if d_str <= end]


def _quad_spec_from_args(
    quadratic: bool,
    quadratic_for: list[str] | None,
    predictors: list[str],
) -> bool | list[bool] | None:
    """Normalize --quadratic / --quadratic-for into a fit spec.

    Returns a bool (all/none), a per-predictor mask, or None on a usage
    error (message already printed to stderr).
    """
    if not quadratic_for:
        return quadratic
    if quadratic:
        print("--quadratic and --quadratic-for are mutually exclusive.", file=sys.stderr)
        return None
    unknown = sorted(set(quadratic_for) - set(predictors))
    if unknown:
        print(
            f"--quadratic-for site(s) not among --predictor: {', '.join(unknown)}",
            file=sys.stderr,
        )
        return None
    quad_for = set(quadratic_for)
    return [p in quad_for for p in predictors]


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Fit OLS regression between USGS daily-mean series. Single "
            "predictor + linear is the default; --predictor is repeatable "
            "for multi-linear; --quadratic adds x² per predictor, "
            "--quadratic-for adds x² for selected predictors only."
        )
    )
    ap.add_argument(
        "--predictor",
        action="append",
        required=True,
        help="USGS site_no for an x column (repeatable for multi-linear)",
    )
    ap.add_argument("--target", required=True, help="USGS site_no for y")
    ap.add_argument("--start", required=True, help="Window start (YYYY-MM-DD)")
    ap.add_argument("--end", required=True, help="Window end (YYYY-MM-DD)")
    ap.add_argument(
        "--name",
        required=True,
        help="Short slug for output filename + heading (e.g. rogue_14328000_from_14330000)",
    )
    ap.add_argument(
        "--quadratic",
        action="store_true",
        help="Include x² for every predictor in addition to x.",
    )
    ap.add_argument(
        "--quadratic-for",
        action="append",
        default=None,
        metavar="SITE",
        help=(
            "Include x² for just this predictor (repeatable). The site must "
            "also be given via --predictor. Mutually exclusive with "
            "--quadratic. Use when residual curvature tracks one predictor "
            "and the other squared terms are not significant."
        ),
    )
    ap.add_argument(
        "--out",
        type=Path,
        help="Markdown output path. If omitted, prints to stdout.",
    )
    ap.add_argument(
        "--calc-handle",
        action="append",
        default=None,
        help=(
            "Per-predictor reference handle for the SQL stub, of the form "
            "PREFIX::GAUGE_NAME (e.g. rp::14330000). Repeat once per "
            "predictor, in the same order as --predictor. "
            "Default: pX::<predictor_site_no> with pX chosen from p1..p9."
        ),
    )
    ap.add_argument(
        "--stability-starts",
        nargs="+",
        default=None,
        help=(
            "Window-start dates for the stability sweep. "
            "Default: {start-5y, start, start+5y, start+10y, start+15y, "
            "earliest-overlap, 1990-01-01}, capped at the window end."
        ),
    )
    ap.add_argument(
        "--leadlag",
        default=None,
        help=(
            "Slug of a gauge_lead_lag.py JSON summary (a sibling of --out) to embed "
            "as a 'Sub-daily lead/lag' section, e.g. mckenzie_14159000_leadlag."
        ),
    )
    ap.add_argument(
        "--deploy-note",
        default=None,
        help=(
            "Deployment-composition warning for the calc_expression section, "
            "for fits whose deployed expression differs from the fitted one "
            "(e.g. the estimate is summed with a live gauge, or drainage-area "
            "scaled). Rides in the Reproduce snippet so a regen keeps it."
        ),
    )
    args = ap.parse_args()
    leadlag = _load_leadlag(args.leadlag, args.out)

    predictors: list[str] = args.predictor

    quad_spec = _quad_spec_from_args(args.quadratic, args.quadratic_for, predictors)
    if quad_spec is None:
        return 1

    print(
        f"Fetching target {args.target} and {len(predictors)} predictor(s) ...",
        file=sys.stderr,
    )
    target_data = fetch_daily_means(args.target)
    predictor_data = [fetch_daily_means(p) for p in predictors]

    overlap_all = sorted(
        set(target_data) & set[str].intersection(*(set(d) for d in predictor_data))
    )
    if not overlap_all:
        print("No overlap across all gauges.", file=sys.stderr)
        return 1

    keys = [d for d in overlap_all if args.start <= d <= args.end]
    if len(keys) < 10:
        print(
            f"Only {len(keys)} overlap points in window — need ≥10.",
            file=sys.stderr,
        )
        return 1

    pts_predictors = [[d[k] for k in keys] for d in predictor_data]
    pts_y = [target_data[k] for k in keys]

    fit = fit_ols(pts_predictors, pts_y, quad_spec)
    coef_unc = coef_uncertainty(pts_predictors, pts_y, keys, quad_spec, fit)

    # Stability windows
    if args.stability_starts is None:
        args.stability_starts = _default_stability_starts(args.start, args.end, overlap_all[0])

    stab = stability_windows(
        target_data, predictor_data, args.end, args.stability_starts, quad_spec
    )

    # Calc handles
    if args.calc_handle:
        if len(args.calc_handle) != len(predictors):
            print(
                f"--calc-handle given {len(args.calc_handle)} times but "
                f"there are {len(predictors)} predictors.",
                file=sys.stderr,
            )
            return 1
        calc_handles = args.calc_handle
    else:
        calc_handles = [f"p{i}::{p}" for i, p in enumerate(predictors, start=1)]

    md = render_markdown(
        name=args.name,
        predictor_sites=predictors,
        target_site=args.target,
        window_start=args.start,
        window_end=args.end,
        quadratic=quad_spec,
        target_data=target_data,
        predictor_data=predictor_data,
        overlap_keys=overlap_all,
        window_keys=keys,
        pts_predictors=pts_predictors,
        pts_y=pts_y,
        fit=fit,
        coef_unc=coef_unc,
        stab=stab,
        calc_handles=calc_handles,
        leadlag=leadlag,
        deploy_note=args.deploy_note,
    )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md)
        print(f"Wrote {args.out}", file=sys.stderr)
        # Sibling artifacts (only when --out is given — stdout mode keeps the
        # script usable as a quick pipe-target).
        svg_path = args.out.with_suffix(".svg")
        json_path = args.out.with_suffix(".json")
        svg_path.write_text(
            _render_residuals_svg(
                slug=args.name,
                fit=fit,
                pivot_predictor=pts_predictors[0],
                target_site=args.target,
                predictor_site=predictors[0],
            )
        )
        print(f"Wrote {svg_path}", file=sys.stderr)
        json_path.write_text(
            _render_fit_json(
                slug=args.name,
                fit=fit,
                target_site=args.target,
                predictor_sites=predictors,
                quadratic=quad_spec,
                window_start=args.start,
                window_end=args.end,
            )
        )
        print(f"Wrote {json_path}", file=sys.stderr)
    else:
        print(md)

    # One-line summary to stderr
    short = ", ".join(
        f"{n}={c:+.4g}±{s:.4g}" for n, c, s in zip(fit.coef_names, fit.coefs, fit.se, strict=False)
    )
    print(
        f"\nSUMMARY: {args.target} ~ {short}, r²={fit.r2:.4f}, RMSE={fit.rmse:.1f}, n={fit.n}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
