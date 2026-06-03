#!/usr/bin/env python3
"""Quantify the sub-daily lead/lag (travel-time) structure of a daily-mean
gauge regression — companion to ``gauge_pair_linear.py``.

Motivation
----------
``gauge_pair_linear.py`` fits ``target ~ predictors`` on USGS **daily means**.
A daily fit averages away the sub-daily travel time between gauges — an
upstream gauge's reading describes water that has not yet reached the target,
a downstream gauge's describes water that already passed it. This script
measures that timing structure directly from USGS **unit values**: it resamples
to a common sub-hourly UTC grid (default 30 min), estimates each predictor's
travel-time lag by cross-correlating *first differences* (the propagating flow
*changes*, not the slowly-varying baseline), and compares the regression's
accuracy with predictors aligned **contemporaneously** vs **travel-time-aligned**.

It reports two alignments, because they answer different questions:

* **full** — every identifiable predictor shifted to its best lag, including
  *downstream* predictors shifted to a *future* reading. This measures the
  total sub-daily timing signal but is **not** realisable in real time.
* **deployable (causal)** — only *upstream* predictors (whose aligned reading
  is in the *past*) are shifted; downstream and unidentifiable predictors are
  held contemporaneous. This is what a real-time nowcast could actually use.

Resolution
----------
The cross-correlation can only resolve timing as fine as the *coarser* of the
two series; for a retired target recorded at 30 min, 30 min is the floor, and
finer grids add noise without information. ``--grid-minutes`` exposes the knob.

Data notes
----------
- Pre-2007 USGS unit values are served **only** by the ``nwis.waterservices``
  host, and the ``parameterCd`` filter suppresses some old discharge series —
  so we fetch *unfiltered* and pick the value column by name.
- **Flow vs stage:** agencies retain discharge (``00060``) longer than gage
  height (``00065``); for *timing* either works (USGS derives flow from stage
  instantaneously via a single-valued rating, so the two are time-locked). We
  prefer flow and fall back to stage per gauge. The RMSE comparison applies the
  daily *flow* coefficients, so it is only emitted when every series is flow.

Standalone — numpy + curl + Python stdlib, no kayak imports (same contract as
``gauge_pair_linear.py``); imports the sibling ``_resample`` helper.
"""

from __future__ import annotations

import argparse
import math
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from _resample import block_bootstrap, lag1_autocorr

# America/Los_Angeles is the site-local clock for every gauge here; USGS stamps
# each unit value PST or PDT explicitly, so we convert with the row's own tz_cd
# rather than re-deriving DST. Value is hours to ADD to local to reach UTC.
TZ_OFFSET_HOURS = {"PST": 8, "PDT": 7, "MST": 7, "MDT": 6}
_EPOCH = datetime(1970, 1, 1)
SECONDS_PER_HOUR = 3600
DEFAULT_GRID_MIN = 30
# Prefer discharge; fall back to gage height (retained for shorter spans).
PARAM_PREFERENCE = ("00060", "00065")
PARAM_NAME = {"00060": "flow", "00065": "stage"}

# A predictor's lag is only trusted if its first-difference CCF peak clears
# this correlation. Regulated tributaries whose sub-daily changes are
# independent of the target produce a flat, near-zero CCF whose argmax is
# noise; below this floor we hold the predictor contemporaneous.
MIN_IDENTIFIABLE_CORR = 0.15


@dataclass(frozen=True)
class LagResult:
    """First-difference cross-correlation outcome for one predictor.

    Lags are stored in integer grid *steps* (the alignment unit); the hour
    values are derived via ``step_seconds`` for display.
    """

    site: str
    best_lag_steps: int  # +ve = predictor LEADS target (upstream); -ve = lags (downstream)
    best_corr: float
    curve: list[tuple[float, float]]  # (lag_hours, corr) over the searched range
    identifiable: bool  # peak Δ-corr cleared MIN_IDENTIFIABLE_CORR
    applied_lag_steps: int  # lag actually used in the FULL alignment (0 if not identifiable)
    step_seconds: int
    travel_note: str

    @property
    def best_lag_h(self) -> float:
        return self.best_lag_steps * self.step_seconds / SECONDS_PER_HOUR

    @property
    def applied_lag_h(self) -> float:
        return self.applied_lag_steps * self.step_seconds / SECONDS_PER_HOUR

    @property
    def deployable(self) -> bool:
        """Upstream (+lag) alignment reads a *past* value — causal/deployable."""
        return self.applied_lag_steps > 0


# ---------------------------------------------------------------------------
# Fetch + resample
# ---------------------------------------------------------------------------
def _fetch_raw_year(site: str, year: int) -> str:
    """Raw RDB of one site's unit values for one calendar year (cached).

    Deliberately *unfiltered* (no ``parameterCd``): the old discharge series for
    some sites is suppressed when the filter is supplied. We pick the value
    column by name downstream.
    """
    cache = Path(f"/tmp/leadlag_{site}_{year}.tsv")
    if not cache.exists() or cache.stat().st_size < 200:
        url = (
            "https://nwis.waterservices.usgs.gov/nwis/iv/"
            f"?format=rdb&sites={site}"
            f"&startDT={year}-01-01&endDT={year}-12-31"
        )
        subprocess.run(["curl", "-sL", url, "-o", str(cache)], check=True)
    return cache.read_text()


def _available_params(text: str) -> set[str]:
    """Which value parameters the RDB header carries (e.g. {'00060','00065'})."""
    out: set[str] = set()
    for line in text.splitlines():
        if line.startswith("agency_cd"):
            for nm in line.split("\t"):
                for p in PARAM_PREFERENCE:
                    if nm.endswith(f"_{p}"):
                        out.add(p)
            break
    return out


def _parse_iv_rdb(text: str, param: str) -> list[tuple[int, float]]:
    """Parse a USGS IV RDB into (utc_epoch_seconds, value) pairs for ``param``.

    Selects the first column whose header ends ``_<param>``, skips the
    ``5s 15s ...`` format-spec row, and converts each local timestamp to UTC
    via its explicit ``tz_cd``.
    """
    col_idx: int | None = None
    out: list[tuple[int, float]] = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if parts[0] == "agency_cd":
            col_idx = next(
                (i for i, name in enumerate(parts) if name.endswith(f"_{param}")),
                None,
            )
            continue
        if parts[0] != "USGS" or col_idx is None or len(parts) <= col_idx:
            continue
        offset = TZ_OFFSET_HOURS.get(parts[3])
        if offset is None:
            continue
        try:
            value = float(parts[col_idx])
        except ValueError:
            continue  # "Ice", "Eqp", "" — non-numeric quality flags
        local = datetime.strptime(parts[2], "%Y-%m-%d %H:%M")
        epoch = int(((local + timedelta(hours=offset)) - _EPOCH).total_seconds())
        out.append((epoch, value))
    return out


def fetch_grid(site: str, years: range, step: int) -> tuple[dict[int, float], str | None]:
    """Resample a site's unit values onto a ``step``-second UTC grid.

    Picks a single value parameter for the whole window — preferring flow
    (``00060``) and falling back to stage (``00065``) — so the series is
    dimensionally consistent. Returns ``(grid, param)`` (param is None if the
    site has no usable unit values).
    """
    texts = [_fetch_raw_year(site, y) for y in years]
    avail: set[str] = set()
    for t in texts:
        avail |= _available_params(t)
    param = next((p for p in PARAM_PREFERENCE if p in avail), None)
    if param is None:
        return {}, None
    buckets: dict[int, list[float]] = {}
    for t in texts:
        for epoch, value in _parse_iv_rdb(t, param):
            buckets.setdefault(epoch - (epoch % step), []).append(value)
    return {g: sum(v) / len(v) for g, v in buckets.items()}, param


def fetch_daily_means(site: str) -> dict[str, float]:
    """USGS daily-mean discharge, reusing the ``gauge_pair_linear`` /tmp cache."""
    cache = Path(f"/tmp/{site}_dv.tsv")
    if not cache.exists() or cache.stat().st_size < 1000:
        url = (
            "https://waterservices.usgs.gov/nwis/dv/"
            f"?format=rdb&sites={site}"
            "&startDT=1900-01-01&endDT=2099-12-31"
            "&parameterCd=00060&statCd=00003"
        )
        subprocess.run(["curl", "-sL", url, "-o", str(cache)], check=True)
    out: dict[str, float] = {}
    for line in cache.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if parts[0] != "USGS" or len(parts) < 4:
            continue
        try:
            out[parts[2]] = float(parts[3])
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------------------
# OLS + lag estimation
# ---------------------------------------------------------------------------
def _design(cols: list[np.ndarray]) -> np.ndarray:
    """Intercept + predictor columns."""
    return np.column_stack([np.ones(len(cols[0])), *cols])


def ols(cols: list[np.ndarray], y: np.ndarray) -> np.ndarray:
    coefs, *_ = np.linalg.lstsq(_design(cols), y, rcond=None)
    return np.asarray(coefs, dtype=float)


def eval_fit(cols: list[np.ndarray], y: np.ndarray, coefs: np.ndarray) -> tuple[float, float]:
    """Return (RMSE, r²) of ``coefs`` applied to ``cols`` against ``y``."""
    if len(y) == 0:
        return float("nan"), float("nan")
    resid = y - _design(cols) @ coefs
    rss = float(resid @ resid)
    tss = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - rss / tss if tss > 0 else float("nan")
    return math.sqrt(rss / len(y)), r2


def _deltas(series: dict[int, float], step: int) -> dict[int, float]:
    """First differences on the regular grid: Δ(g) needs g and g-step."""
    return {g: series[g] - series[g - step] for g in series if (g - step) in series}


def ccf_curve(
    target_g: dict[int, float],
    pred_g: dict[int, float],
    max_steps: int,
    step: int,
) -> list[tuple[int, float]]:
    """First-difference cross-correlation, returned as ``[(lag_steps, corr)]``.

    For each candidate lag of ``k`` grid steps, the predictor's *change* at
    ``g - k*step`` is correlated with the target's change at ``g``. Positive k
    peaks for an **upstream** gauge (its rise reaches the target later); negative
    k for **downstream**. First differences isolate the propagating signal from
    the near-identical slowly-varying baseline.
    """
    dy = _deltas(target_g, step)
    dx = _deltas(pred_g, step)
    curve: list[tuple[int, float]] = []
    for k in range(-max_steps, max_steps + 1):
        shift = k * step
        common = [g for g in dy if (g - shift) in dx]
        if len(common) < 100:
            continue
        a = np.array([dy[g] for g in common])
        b = np.array([dx[g - shift] for g in common])
        if a.std() == 0 or b.std() == 0:
            continue
        curve.append((k, float(np.corrcoef(a, b)[0, 1])))
    return curve


def classify_lag(site: str, curve_k: list[tuple[int, float]], step: int) -> LagResult:
    """Pick the peak lag and decide whether it's trustworthy."""
    if not curve_k:
        return LagResult(site, 0, float("nan"), [], False, 0, step, "no sub-daily overlap")
    best_k, best_corr = max(curve_k, key=lambda kc: kc[1])
    identifiable = best_corr >= MIN_IDENTIFIABLE_CORR
    applied = best_k if identifiable else 0
    lag_h = best_k * step / SECONDS_PER_HOUR
    if not identifiable:
        note = f"not identifiable (peak Δ-corr {best_corr:.2f}); held contemporaneous"
    elif best_k > 0:
        note = f"upstream — rise reaches target ~{lag_h:.1f} h later (deployable)"
    elif best_k < 0:
        note = f"downstream — target leads it by ~{-lag_h:.1f} h (future read, not deployable)"
    else:
        note = "co-located / sub-grid travel"
    curve_h = [(k * step / SECONDS_PER_HOUR, c) for k, c in curve_k]
    return LagResult(site, best_k, best_corr, curve_h, identifiable, applied, step, note)


def aligned_columns(
    predictors: list[str],
    grid: dict[str, dict[int, float]],
    lags_steps: dict[str, int],
    points: list[int],
    step: int,
) -> list[np.ndarray]:
    """Predictor columns for ``points``, each shifted by its per-site lag (steps)."""
    out: list[np.ndarray] = []
    for site in predictors:
        shift = lags_steps[site] * step
        out.append(np.array([grid[site][g - shift] for g in points]))
    return out


def common_points(
    target_g: dict[int, float],
    predictors: list[str],
    grid: dict[str, dict[int, float]],
    full_lags: dict[str, int],
    step: int,
) -> list[int]:
    """Grid points where the target, every contemporaneous predictor, AND every
    full-lag-shifted predictor all exist — one shared hold-out so every
    alignment (contemporaneous / full / deployable) uses identical points.
    (Deployable shifts are a subset of {0, full}, so they are covered too.)"""
    out = []
    for g in target_g:
        ok = True
        for site in predictors:
            shift = full_lags[site] * step
            if g not in grid[site] or (g - shift) not in grid[site]:
                ok = False
                break
        if ok:
            out.append(g)
    return sorted(out)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _render_ccf_svg(lag_results: list[LagResult]) -> str:
    """Lag (h) on x, first-difference correlation on y; one line per predictor
    with a marker at its peak. Legend uses each predictor's USGS id."""
    palette = ["#1b5591", "#c0392b", "#27ae60", "#8e44ad", "#d35400", "#16a085"]
    all_lags = sorted({lag for r in lag_results for lag, _ in r.curve})
    all_corr = [c for r in lag_results for _, c in r.curve]
    if not all_lags or not all_corr:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>\n'
    x_lo, x_hi = all_lags[0], all_lags[-1]
    y_lo, y_hi = min(0.0, min(all_corr)), max(all_corr)
    y_hi = math.ceil(y_hi * 10) / 10
    y_lo = math.floor(y_lo * 10) / 10
    if x_hi <= x_lo:
        x_hi = x_lo + 1
    if y_hi <= y_lo:
        y_hi = y_lo + 0.1

    w, h = 640, 400
    ml, mr, mt, mb = 60, 140, 40, 50
    pw, ph = w - ml - mr, h - mt - mb

    def xpx(x: float) -> float:
        return ml + (x - x_lo) / (x_hi - x_lo) * pw

    def ypx(y: float) -> float:
        return mt + (y_hi - y) / (y_hi - y_lo) * ph

    p: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="-apple-system,BlinkMacSystemFont,'
        'Segoe UI,Roboto,sans-serif" font-size="12">',
        f'<rect x="0" y="0" width="{w}" height="{h}" fill="#fff"/>',
        f'<text x="{w / 2}" y="22" text-anchor="middle" font-size="14" font-weight="600">'
        "First-difference cross-correlation vs lag (travel time)</text>",
        f'<rect x="{ml}" y="{mt}" width="{pw}" height="{ph}" fill="none" '
        'stroke="#999" stroke-width="1"/>',
    ]
    x_step = 6 if (x_hi - x_lo) > 24 else 3
    x = math.ceil(x_lo / x_step) * x_step
    while x <= x_hi:
        px = xpx(x)
        p.append(
            f'<line x1="{px:.1f}" y1="{mt + ph}" x2="{px:.1f}" y2="{mt + ph + 5}" stroke="#999"/>'
        )
        p.append(f'<text x="{px:.1f}" y="{mt + ph + 18}" text-anchor="middle">{x:+.0f}</text>')
        x += x_step
    yt = y_lo
    while yt <= y_hi + 1e-9:
        py = ypx(yt)
        p.append(f'<line x1="{ml - 5}" y1="{py:.1f}" x2="{ml}" y2="{py:.1f}" stroke="#999"/>')
        p.append(f'<text x="{ml - 8}" y="{py + 4:.1f}" text-anchor="end">{yt:.1f}</text>')
        yt += 0.2
    zx = xpx(0)
    p.append(
        f'<line x1="{zx:.1f}" y1="{mt}" x2="{zx:.1f}" y2="{mt + ph}" '
        'stroke="#333" stroke-width="1" stroke-dasharray="2,2"/>'
    )
    for i, r in enumerate(lag_results):
        colour = palette[i % len(palette)]
        dash = "" if r.identifiable else ' stroke-dasharray="4,3"'
        pts = " ".join(f"{xpx(lag):.1f},{ypx(c):.1f}" for lag, c in r.curve)
        p.append(
            f'<polyline points="{pts}" fill="none" stroke="{colour}" stroke-width="1.8"{dash}/>'
        )
        if r.identifiable:
            p.append(
                f'<circle cx="{xpx(r.best_lag_h):.1f}" cy="{ypx(r.best_corr):.1f}" r="3.5" '
                f'fill="{colour}"/>'
            )
        ly = mt + 14 + i * 18
        tag = f"{r.applied_lag_h:+.1f} h" if r.identifiable else "n/a"
        p.append(
            f'<line x1="{ml + pw + 8}" y1="{ly - 4}" x2="{ml + pw + 26}" y2="{ly - 4}" '
            f'stroke="{colour}" stroke-width="2.5"{dash}/>'
        )
        p.append(f'<text x="{ml + pw + 30}" y="{ly}" font-size="10">{r.site} ({tag})</text>')
    p.append(
        f'<text x="{ml + pw / 2}" y="{h - 12}" text-anchor="middle">'
        "predictor lag τ (hours; + = predictor leads target)</text>"
    )
    p.append(
        f'<text x="16" y="{mt + ph / 2}" text-anchor="middle" '
        f'transform="rotate(-90 16 {mt + ph / 2})">corr( Δpredictor , Δtarget )</text>'
    )
    p.append("</svg>")
    return "".join(p) + "\n"


def _pct(a: float, b: float) -> float:
    """Percent reduction from a to b (positive = improvement)."""
    return 100.0 * (a - b) / a if a else float("nan")


def _excludes_zero(ci: dict) -> bool:
    return bool(ci["lo"] > 0 or ci["hi"] < 0)


def render_markdown(
    *,
    name: str,
    daily_doc: str,
    target: str,
    predictors: list[str],
    labels: dict[str, str],
    params: dict[str, str],
    start: str,
    end: str,
    step_min: int,
    n_points: int,
    rmse_valid: bool,
    lag_results: list[LagResult],
    rows: list[dict],
    storm: dict | None,
    boot: dict,
    daily_full_rmse: float,
    daily_full_r2: float,
    daily_full_n: int,
    daily_window: tuple[str, str],
) -> str:
    """Assemble the lead/lag analysis report."""
    by_site = {r.site: r for r in lag_results}
    is_mckenzie = target == DEFAULT_TARGET
    per_year = 525600 / step_min  # grid points per year
    L: list[str] = []
    a = L.append

    a(f"# Sub-daily lead/lag: USGS {target} regression\n")
    a(
        "Companion to "
        "[`gauge_pair_linear.py`](../../scripts/regression/gauge_pair_linear.py) and the "
        f"daily-mean fit in [`{daily_doc}.md`](./{daily_doc}.md). "
        "**Question (informational):** the daily-mean fit averages away the sub-daily "
        "travel time between gauges — how large is that timing structure, and how much of "
        "it is real-time-usable?\n"
    )
    a(f"![CCF vs lag](./{name}.svg)\n")

    cmd = " \\\n    ".join(
        ["python3 scripts/regression/gauge_lead_lag.py"]
        + [f"--predictor {s}" for s in predictors]
        + [
            f"--target {target}",
            f"--start {start}",
            f"--end {end}",
            f"--grid-minutes {step_min}",
            f"--name {name}",
        ]
    )
    a(f"Generated by:\n\n```bash\n{cmd}\n```\n")

    a("## Data\n")
    a(
        f"USGS **unit values** resampled to a common **{step_min}-min** UTC grid over "
        f"**{start} → {end}**. Overlap where the target and all {len(predictors)} "
        f"predictors have a value: **{n_points:,} points** (~{n_points / per_year:.1f} "
        "years). Each gauge uses discharge where available, else gage height (timing is "
        "identical — USGS derives flow from stage instantaneously):\n"
    )
    a("| Role | Gauge | Label | variable |")
    a("|---|---|---|---|")
    a(
        f"| target | `{target}` | {labels.get(target, '')} | {PARAM_NAME.get(params.get(target, ''), '?')} |"
    )
    for s in predictors:
        a(f"| predictor | `{s}` | {labels.get(s, '')} | {PARAM_NAME.get(params.get(s, ''), '?')} |")
    a("")
    if is_mckenzie:
        a(
            "> Note: the deployed daily fit uses **5** predictors; SF Cougar `14159200` "
            "is excluded here (its unit-value record starts in 2000, after the target "
            "retired in 1994). The daily reference below is refit on the same 4 "
            "predictors for an apples-to-apples comparison.\n"
        )

    a("## Estimated travel-time lags\n")
    a(
        "Per predictor, the lag τ maximizing the correlation of *first differences* (flow "
        f"changes) with the target, searched in {step_min}-min steps. **+τ** = upstream "
        "(predictor leads the target; its aligned reading is a *past* value, so it is "
        "**deployable** in real time); **-τ** = downstream (its aligned reading is a "
        f"*future* value, **not** deployable). A peak below **{MIN_IDENTIFIABLE_CORR:.2f}** "
        "has no resolvable travel time and is held contemporaneous.\n"
    )
    a("| Predictor | peak τ (h) | peak Δ-corr | applied τ (h) | interpretation |")
    a("|---|---|---|---|---|")
    for s in predictors:
        r = by_site[s]
        a(
            f"| {labels.get(s, s)} `{s}` | {r.best_lag_h:+.1f} | {r.best_corr:.3f} | "
            f"**{r.applied_lag_h:+.1f}** | {r.travel_note} |"
        )
    a("")

    if not rmse_valid:
        a(
            "> The accuracy comparison below is **omitted**: it applies the daily *flow* "
            "regression coefficients, but at least one series here is gage height, so the "
            "units don't match. The travel-time lags above (timing only) are still valid.\n"
        )
        return "\n".join(L) + "\n"

    bf, bd = boot["full"], boot["deploy"]
    full_gain = _pct(
        next(r["rmse"] for r in rows if r["align_key"] == "con"),
        next(r["rmse"] for r in rows if r["align_key"] == "full" and r["coefs"] == "daily-trained"),
    )
    deploy_gain = _pct(
        next(r["rmse"] for r in rows if r["align_key"] == "con"),
        next(
            r["rmse"] for r in rows if r["align_key"] == "deploy" and r["coefs"] == "daily-trained"
        ),
    )

    a("## Accuracy: contemporaneous vs travel-time-aligned\n")
    a(
        "All alignments share one hold-out grid (only the alignment changes). "
        "**daily-trained** = the deployed-style daily coefficients applied to the grid "
        "values (production-relevant); **hourly-refit** = coefficients refit on the grid "
        "itself (an upper bound). **full** shifts every identifiable predictor (incl. "
        "downstream → future); **deployable** shifts only upstream predictors (causal).\n"
    )
    a("| Coefficients | Alignment | n | r² | RMSE (cfs) |")
    a("|---|---|---|---|---|")
    for row in rows:
        a(
            f"| {row['coefs']} | {row['alignment']} | {row['n']:,} | "
            f"{row['r2']:.4f} | {row['rmse']:.1f} |"
        )
    a("")
    a(
        f"Daily-mean reference (same {len(predictors)} predictors, {daily_window[0]}→"
        f"{daily_window[1]}, n={daily_full_n:,}): RMSE **{daily_full_rmse:.1f} cfs**, r² "
        f"{daily_full_r2:.4f} — daily means are smoother than instantaneous values, so "
        "this sits below the grid RMSEs and isn't directly comparable to them.\n"
    )

    a("### Is the gain statistically real, and is it usable?\n")
    a(
        f"Grid residuals are strongly autocorrelated (lag-1 **{boot['resid_rho1']:.2f}**), "
        f"so the {n_points:,} points carry far fewer independent observations than their "
        "count. A **block bootstrap** over 7-day blocks "
        f"({boot['n_blocks']} of them, B=2000) on the RMSE reduction (contemporaneous "
        "minus aligned):\n"
    )
    a("| Alignment | gain | mean Δ (cfs) | 95% CI (cfs) | better in | resolved? |")
    a("|---|---|---|---|---|---|")
    a(
        f"| **full** (incl. downstream future) | {full_gain:+.1f}% | {bf['mean']:+.2f} | "
        f"[{bf['lo']:+.2f}, {bf['hi']:+.2f}] | {bf['p_pos']:.0f}% | "
        f"{'yes' if _excludes_zero(bf) else 'no (CI ∋ 0)'} |"
    )
    a(
        f"| **deployable** (causal, upstream) | {deploy_gain:+.1f}% | {bd['mean']:+.2f} | "
        f"[{bd['lo']:+.2f}, {bd['hi']:+.2f}] | {bd['p_pos']:.0f}% | "
        f"{'yes' if _excludes_zero(bd) else 'no (CI ∋ 0)'} |"
    )
    a("")

    if storm is not None:
        a(
            f"During the **fastest-changing {storm['pct']:.0f}% of points** "
            f"(|Δtarget| ≥ {storm['thresh']:.0f} cfs/{step_min}min, n={storm['n']:,}), where "
            "misalignment should bite hardest, full alignment changes RMSE by "
            f"**{_pct(storm['con_rmse'], storm['lag_rmse']):+.1f}%** "
            f"({storm['con_rmse']:.1f} → {storm['lag_rmse']:.1f} cfs).\n"
        )

    # Verdict: the deployable result governs real-time usability; the full
    # result characterises the total signal (which may be non-causal).
    deploy_sig = _excludes_zero(bd)
    full_sig = _excludes_zero(bf)
    any_lag = any(r.applied_lag_steps != 0 for r in lag_results)
    any_upstream = any(r.deployable for r in lag_results)
    a("## Verdict\n")
    if not any_lag:
        a(
            f"**No resolvable sub-daily lag.** Every predictor is co-located within the "
            f"{step_min}-min grid or below the identifiability floor "
            f"({MIN_IDENTIFIABLE_CORR:.2f}), so there is nothing to align — full and "
            "deployable alignment are both identical to contemporaneous. **Keep "
            "contemporaneous readings.**\n"
        )
    elif deploy_sig and deploy_gain >= 2.0:
        a(
            f"**A usable sub-daily gain exists here.** The deployable (causal, upstream) "
            f"alignment lowers RMSE by **{deploy_gain:+.1f}%** with a 95% CI that excludes "
            f"zero ([{bd['lo']:+.2f}, {bd['hi']:+.2f}] cfs) — worth considering for a "
            "real-time estimate (see deployability below).\n"
        )
    elif full_sig and not deploy_sig:
        deploy_clause = (
            "Here there are **no upstream predictors** at all, so the deployable "
            "alignment is simply contemporaneous — nothing is usable in real time."
            if not any_upstream
            else f"The **deployable** (upstream-only) gain is **{deploy_gain:+.1f}%** with "
            f"a CI through zero ([{bd['lo']:+.2f}, {bd['hi']:+.2f}] cfs) — nothing usable "
            "in real time."
        )
        a(
            f"**The sub-daily signal is real but not real-time-usable.** Full alignment "
            f"gives a statistically-resolved **{full_gain:+.1f}%** (CI "
            f"[{bf['lo']:+.2f}, {bf['hi']:+.2f}] cfs excludes zero), but that gain comes "
            "from **downstream** predictors aligned to *future* readings — a downstream "
            "gauge's reading τ ahead is essentially a direct measurement of the target's "
            f"current water arriving later, i.e. look-ahead. {deploy_clause} "
            "**Keep contemporaneous readings.**\n"
        )
    else:
        a(
            f"**Negligible and statistically unresolved.** The block-bootstrap CI includes "
            f"zero (full {full_gain:+.1f}% [{bf['lo']:+.2f}, {bf['hi']:+.2f}] cfs; "
            f"deployable {deploy_gain:+.1f}% [{bd['lo']:+.2f}, {bd['hi']:+.2f}] cfs) — once "
            "the residual autocorrelation is accounted for, the improvement isn't "
            "distinguishable from no effect. **Keep contemporaneous readings.**\n"
        )

    a("### Deployability (what it *would* take)\n")
    a(
        "Applying lags in production is **not** a coefficient change; it requires the "
        "calculator to read a predictor's value *from τ ago* rather than its latest:\n\n"
        "1. **Upstream predictors (+τ):** deployable — the value is in the past, already "
        "in the `observation` table; select the reading closest to `now - τ`.\n"
        "2. **Downstream predictors (-τ):** **not** deployable for a nowcast — the "
        "best-aligned value is in the future. Leave them contemporaneous, or treat the "
        "estimate as a short forecast.\n"
        "3. **Plumbing:** `calc_expression` references only `LatestObservation`; a "
        "lag-aware estimate needs a time-offset reference form and a windowed lookup in "
        "`kayak.cli.calculator` — justified only when the deployable share is material.\n"
    )

    a("## Method\n")
    a(
        f"- **Unit values** pulled unfiltered from `nwis.waterservices.usgs.gov` and "
        f"resampled to a {step_min}-min grid (discharge preferred, gage height as "
        "fallback — time-locked, so either works for timing).\n"
        "- **Lag estimation** maximizes the correlation of first differences (flow "
        "*changes* propagate; baseline levels are near-identical across neighbours). "
        "Resolution is capped by the coarser series — a 30-min target can't resolve "
        "finer than 30 min, and finer grids add noise without information.\n"
        "- **Causal split:** *deployable* shifts only upstream predictors (past reads); "
        "*full* also shifts downstream predictors to future reads (not real-time-usable, "
        "but it bounds the total timing signal).\n"
        "- **Significance:** the RMSE difference is block-bootstrapped over 7-day blocks "
        "(B=2000) so the CI reflects the effective, not nominal, sample size (longer "
        "blocks would only widen it — a conservative bound).\n"
        f"- **Caveat:** the grid hold-out ({start}..{end}, ~{n_points / per_year:.1f} yr) "
        "is far shorter than the daily fit's record"
        + (" and excludes SF Cougar" if is_mckenzie else "")
        + "; the daily-reference row controls for the predictor-set change, not the window.\n"
    )
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
DEFAULT_TARGET = "14159000"
DEFAULT_PREDICTORS = ["14162500", "14158850", "14159500", "14161500"]
DEFAULT_START = "1987-10-01"
DEFAULT_END = "1994-09-30"
DEFAULT_NAME = "mckenzie_14159000_leadlag"
DEFAULT_DAILY_DOC = "mckenzie_14159000_from_vida_trailbridge_sfrainbow_sfcougar_lookout"
LABELS: dict[str, str] = {
    "14159000": "McKenzie at McKenzie Bridge (target, retired)",
    "14162500": "McKenzie nr Springfield / Vida (downstream)",
    "14158850": "McKenzie at Trail Bridge Dam (upstream, dominant)",
    "14159500": "SF McKenzie nr Rainbow",
    "14161500": "Lookout Cr nr Blue River",
}


def _window_epochs(start: str, end: str) -> tuple[int, int]:
    lo = int((datetime.strptime(start, "%Y-%m-%d") - _EPOCH).total_seconds())
    hi = int((datetime.strptime(end, "%Y-%m-%d") - _EPOCH).total_seconds()) + 86400
    return lo, hi


def _ci(reps: np.ndarray) -> dict:
    lo, hi = np.percentile(reps, [2.5, 97.5])
    return {
        "mean": float(reps.mean()),
        "lo": float(lo),
        "hi": float(hi),
        "p_pos": 100.0 * float((reps > 0).mean()),
    }


def _diff_stat(ec: np.ndarray, el: np.ndarray) -> Callable[[np.ndarray], float]:
    """+ve => the alignment lowers RMSE on the resampled points."""
    return lambda idx: float(np.sqrt((ec[idx] ** 2).mean()) - np.sqrt((el[idx] ** 2).mean()))


def main() -> int:
    ap = argparse.ArgumentParser(description="Sub-daily lead/lag of a gauge regression.")
    ap.add_argument("--target", default=DEFAULT_TARGET)
    ap.add_argument("--predictor", action="append", dest="predictors")
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=DEFAULT_END)
    ap.add_argument("--name", default=DEFAULT_NAME)
    ap.add_argument("--daily-doc", default=DEFAULT_DAILY_DOC, help="Sibling daily report slug.")
    ap.add_argument("--grid-minutes", type=int, default=DEFAULT_GRID_MIN, help="Resample grid.")
    ap.add_argument("--max-lag", type=float, default=18.0, help="Search ±this many hours.")
    ap.add_argument("--daily-start", default="1968-10-01", help="Daily-reference window start.")
    ap.add_argument("--out", type=Path, help="Markdown output path (also writes .svg).")
    args = ap.parse_args()
    predictors: list[str] = args.predictors or DEFAULT_PREDICTORS
    sites = [args.target, *predictors]
    step = args.grid_minutes * 60
    max_steps = round(args.max_lag * SECONDS_PER_HOUR / step)
    years = range(int(args.start[:4]), int(args.end[:4]) + 1)
    lo, hi = _window_epochs(args.start, args.end)

    print(f"Fetching unit values, {len(sites)} sites, {step // 60}-min grid ...", file=sys.stderr)
    grid: dict[str, dict[int, float]] = {}
    params: dict[str, str] = {}
    for s in sites:
        full, param = fetch_grid(s, years, step)
        grid[s] = {g: v for g, v in full.items() if lo <= g < hi}
        if param:
            params[s] = param
        print(
            f"  {s}: {len(grid[s]):,} points ({PARAM_NAME.get(param or '', 'none')})",
            file=sys.stderr,
        )

    target_g = grid[args.target]
    if len(target_g) < 100:
        print(f"Target has only {len(target_g)} grid points — aborting.", file=sys.stderr)
        return 1

    # Per-predictor lag (full) + the causal/deployable subset (upstream only).
    lag_results: list[LagResult] = []
    full_lags: dict[str, int] = {}
    deploy_lags: dict[str, int] = {}
    for s in predictors:
        r = classify_lag(s, ccf_curve(target_g, grid[s], max_steps, step), step)
        lag_results.append(r)
        full_lags[s] = r.applied_lag_steps
        deploy_lags[s] = r.applied_lag_steps if r.deployable else 0
        print(f"  lag {s}: peak {r.best_lag_h:+.1f} h (corr {r.best_corr:.3f})", file=sys.stderr)

    points = common_points(target_g, predictors, grid, full_lags, step)
    if len(points) < 100:
        print(f"Only {len(points)} usable points — aborting.", file=sys.stderr)
        return 1
    y = np.array([target_g[g] for g in points])
    cols_con = aligned_columns(predictors, grid, dict.fromkeys(predictors, 0), points, step)
    cols_full = aligned_columns(predictors, grid, full_lags, points, step)
    cols_deploy = aligned_columns(predictors, grid, deploy_lags, points, step)

    # Daily reference + daily-trained (flow) coefficients.
    daily = {s: fetch_daily_means(s) for s in sites}
    daily_end = max(daily[args.target])
    dkeys = sorted(
        set(daily[args.target]) & set[str].intersection(*(set(daily[s]) for s in predictors))
    )
    dwin = [k for k in dkeys if args.daily_start <= k <= daily_end]
    dcols = [np.array([daily[s][k] for k in dwin]) for s in predictors]
    daily_coefs = ols(dcols, np.array([daily[args.target][k] for k in dwin]))
    daily_rmse, daily_r2 = eval_fit(
        dcols, np.array([daily[args.target][k] for k in dwin]), daily_coefs
    )

    # The RMSE comparison applies flow coefficients, so it is only valid when
    # every grid series is flow (00060). Lag estimation (timing) is always valid.
    rmse_valid = all(params.get(s) == "00060" for s in sites)

    rows: list[dict] = []
    storm: dict | None = None
    boot: dict = {"resid_rho1": float("nan"), "n_blocks": 0}
    if rmse_valid:
        coefs_con = ols(cols_con, y)
        coefs_full = ols(cols_full, y)

        def row(
            coefs_label: str,
            align_label: str,
            key: str,
            cols: list[np.ndarray],
            coefs: np.ndarray,
        ) -> dict:
            rmse, r2 = eval_fit(cols, y, coefs)
            return {
                "coefs": coefs_label,
                "alignment": align_label,
                "align_key": key,
                "n": len(points),
                "rmse": rmse,
                "r2": r2,
            }

        rows = [
            row("daily-trained", "contemporaneous", "con", cols_con, daily_coefs),
            row("daily-trained", "full (incl. downstream)", "full", cols_full, daily_coefs),
            row("daily-trained", "deployable (upstream-only)", "deploy", cols_deploy, daily_coefs),
            row("hourly-refit", "contemporaneous", "con", cols_con, coefs_con),
            row("hourly-refit", "full (incl. downstream)", "full", cols_full, coefs_full),
        ]

        e_con = y - _design(cols_con) @ daily_coefs
        e_full = y - _design(cols_full) @ daily_coefs
        e_deploy = y - _design(cols_deploy) @ daily_coefs
        block = np.asarray(points) // (7 * 86400)
        boot = {
            "full": _ci(block_bootstrap(block, _diff_stat(e_con, e_full), n_boot=2000, seed=0)),
            "deploy": _ci(block_bootstrap(block, _diff_stat(e_con, e_deploy), n_boot=2000, seed=0)),
            "resid_rho1": lag1_autocorr(e_con),
            "n_blocks": int(np.unique(block).size),
        }
        print(
            f"  full gain CI=[{boot['full']['lo']:.2f},{boot['full']['hi']:.2f}]  "
            f"deployable CI=[{boot['deploy']['lo']:.2f},{boot['deploy']['hi']:.2f}]",
            file=sys.stderr,
        )

        # Storm subset (full alignment), where misalignment bites hardest.
        prev = np.array(
            [target_g[g] - target_g[g - step] if (g - step) in target_g else np.nan for g in points]
        )
        valid = ~np.isnan(prev)
        thresh = float(np.percentile(np.abs(prev[valid]), 90))
        mask = valid & (np.abs(prev) >= thresh)
        s_con, _ = eval_fit([c[mask] for c in cols_con], y[mask], daily_coefs)
        s_lag, _ = eval_fit([c[mask] for c in cols_full], y[mask], daily_coefs)
        storm = {
            "n": int(mask.sum()),
            "pct": 100.0 * int(mask.sum()) / int(valid.sum()),
            "thresh": thresh,
            "con_rmse": s_con,
            "lag_rmse": s_lag,
        }

    md = render_markdown(
        name=args.name,
        daily_doc=args.daily_doc,
        target=args.target,
        predictors=predictors,
        labels=LABELS,
        params=params,
        start=args.start,
        end=args.end,
        step_min=args.grid_minutes,
        n_points=len(points),
        rmse_valid=rmse_valid,
        lag_results=lag_results,
        rows=rows,
        storm=storm,
        boot=boot,
        daily_full_rmse=daily_rmse,
        daily_full_r2=daily_r2,
        daily_full_n=len(dwin),
        daily_window=(args.daily_start, daily_end),
    )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md)
        args.out.with_suffix(".svg").write_text(_render_ccf_svg(lag_results))
        print(f"Wrote {args.out} and {args.out.with_suffix('.svg')}", file=sys.stderr)
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
