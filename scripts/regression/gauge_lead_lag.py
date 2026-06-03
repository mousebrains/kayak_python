#!/usr/bin/env python3
"""Quantify the sub-daily lead/lag (travel-time) impact on a daily-mean
gauge regression — companion to ``gauge_pair_linear.py``.

Motivation
----------
``gauge_pair_linear.py`` fits ``target ~ predictors`` on USGS **daily means**,
because the target gauge's *daily* record is decades long. But in production
the resulting ``calc_expression`` applies those coefficients to the **latest
instantaneous** readings of each predictor (``LatestObservation`` values).
Neighbouring gauges are separated by river miles, so at any instant their
latest readings are *not* hydrologically contemporaneous: an upstream gauge's
current reading describes water that has not yet reached the target, while a
downstream gauge's current reading describes water that passed the target
hours ago. The daily-mean fit averages that timing offset away and never sees
it; the real-time estimate eats it as error.

This script measures that error directly. It pulls USGS **unit values**
(sub-hourly), resamples to a common hourly UTC grid, estimates each
predictor's travel-time lag by cross-correlating *first differences* (storm
rises/falls, which actually propagate) with the target, then compares the
regression's accuracy with predictors aligned **contemporaneously** (lag 0,
what production does today) versus **travel-time-aligned** (each predictor
shifted by its estimated lag).

Two coefficient sources are evaluated on an identical hold-out grid so the
only thing that changes between the two columns is the *alignment*:

* **daily-trained** — coefficients refit on daily means (the deployed style);
  this is the production-relevant number.
* **hourly-refit** — coefficients refit on the hourly grid itself; the
  best-case ceiling for what lag-alignment can buy.

Data caveat
-----------
Pre-2007 USGS unit values are served **only** by the ``nwis.waterservices``
host, and the ``parameterCd`` filter silently suppresses some old discharge
series — so this script fetches *unfiltered* and selects the ``*_00060``
(discharge) column by name. Any predictor lacking unit values across the
target's UV window is dropped (for McKenzie Bridge that's SF Cougar
``14159200``, whose UV record starts in 2000, after the target retired).

Standalone — depends on numpy + curl + Python stdlib, no kayak imports, so it
runs without the project venv (same contract as ``gauge_pair_linear.py``).
"""

from __future__ import annotations

import argparse
import math
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

# America/Los_Angeles is the site-local clock for every McKenzie gauge; USGS
# stamps each unit value PST or PDT explicitly, so we convert with the row's
# own tz_cd rather than re-deriving DST. Value is hours to ADD to local to
# reach UTC.
TZ_OFFSET_HOURS = {"PST": 8, "PDT": 7}
_EPOCH = datetime(1970, 1, 1)
SECONDS_PER_HOUR = 3600

# A predictor's lag is only trusted if its first-difference CCF peak clears
# this correlation. Regulated tributaries whose sub-daily changes are
# independent of the target (e.g. SF McKenzie below Cougar Dam) produce a
# flat, near-zero CCF whose argmax is meaningless noise; below this floor we
# hold the predictor contemporaneous rather than inject a spurious shift.
MIN_IDENTIFIABLE_CORR = 0.15


@dataclass(frozen=True)
class LagResult:
    """First-difference cross-correlation outcome for one predictor."""

    site: str
    best_lag_h: int  # +ve = predictor LEADS target (upstream); -ve = lags (downstream)
    best_corr: float
    curve: list[tuple[int, float]]  # (lag_h, corr) over the searched range
    identifiable: bool  # peak Δ-corr cleared MIN_IDENTIFIABLE_CORR
    applied_lag_h: int  # lag actually used in alignment (0 when not identifiable)
    travel_note: str


# ---------------------------------------------------------------------------
# Fetch + resample
# ---------------------------------------------------------------------------
def _fetch_raw_year(site: str, year: int) -> str:
    """Raw RDB of one site's unit values for one calendar year (cached).

    Deliberately *unfiltered* (no ``parameterCd``): the old discharge series
    for some sites (e.g. Vida ``14162500``) is suppressed when the filter is
    supplied. We pick the discharge column by name downstream.
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


def _parse_iv_rdb(text: str) -> list[tuple[int, float]]:
    """Parse a USGS IV RDB into (utc_epoch_seconds, discharge_cfs) pairs.

    Selects the first column whose header ends ``_00060`` (discharge), skips
    the ``5s 15s ...`` format-spec row, and converts each local timestamp to
    UTC via its explicit ``tz_cd``.
    """
    col_idx: int | None = None
    out: list[tuple[int, float]] = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if parts[0] == "agency_cd":
            col_idx = next(
                (i for i, name in enumerate(parts) if name.endswith("_00060")),
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


def fetch_hourly(site: str, years: range) -> dict[int, float]:
    """Hourly-mean discharge keyed by the UTC epoch of the hour's start.

    Resampling to an hourly grid (a) puts 15-min and 30-min sites on a common
    clock and (b) damps instrument jitter without smearing across the 1-12 h
    lags we're trying to resolve.
    """
    pairs: list[tuple[int, float]] = []
    for year in years:
        pairs.extend(_parse_iv_rdb(_fetch_raw_year(site, year)))
    buckets: dict[int, list[float]] = {}
    for epoch, value in pairs:
        buckets.setdefault(epoch - (epoch % SECONDS_PER_HOUR), []).append(value)
    return {hour: sum(vals) / len(vals) for hour, vals in buckets.items()}


def fetch_daily_means(site: str) -> dict[str, float]:
    """USGS daily-mean discharge, reusing the ``gauge_pair_linear`` /tmp cache.

    Daily means come from the plain ``waterservices`` host with ``statCd``
    (mean); this matches the cache file ``gauge_pair_linear.py`` already
    writes, so a prior daily run is reused for free.
    """
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


def _hourly_deltas(series: dict[int, float]) -> dict[int, float]:
    """First differences on the regular hourly grid: Δ(h) needs h and h-1h."""
    return {
        h: series[h] - series[h - SECONDS_PER_HOUR]
        for h in series
        if (h - SECONDS_PER_HOUR) in series
    }


def ccf_curve(
    target_h: dict[int, float],
    pred_h: dict[int, float],
    lags_h: range,
) -> list[tuple[int, float]]:
    """First-difference cross-correlation of a predictor against the target.

    For each candidate lag τ (hours) the predictor's *change* at hour ``h-τ``
    is correlated with the target's change at hour ``h``. Positive τ peaks for
    an **upstream** gauge (its rise reaches the target τ hours later); negative
    τ for a **downstream** gauge. First differences are used (not levels)
    because the slowly-varying baseline flow is near-identical across these
    neighbouring gauges and would pin a levels-CCF peak at τ≈0 regardless of
    the true wave travel time. Returns ``[(lag_h, corr), ...]``.
    """
    dy = _hourly_deltas(target_h)
    dx = _hourly_deltas(pred_h)
    curve: list[tuple[int, float]] = []
    for lag_h in lags_h:
        shift = lag_h * SECONDS_PER_HOUR
        common = [h for h in dy if (h - shift) in dx]
        if len(common) < 100:
            continue
        a = np.array([dy[h] for h in common])
        b = np.array([dx[h - shift] for h in common])
        if a.std() == 0 or b.std() == 0:
            continue
        curve.append((lag_h, float(np.corrcoef(a, b)[0, 1])))
    return curve


def classify_lag(site: str, curve: list[tuple[int, float]]) -> LagResult:
    """Pick the peak lag and decide whether it's trustworthy.

    Below ``MIN_IDENTIFIABLE_CORR`` the predictor is held contemporaneous
    (``applied_lag_h = 0``) — a flat CCF means no resolvable travel time, so
    the argmax would be noise.
    """
    if not curve:
        return LagResult(site, 0, float("nan"), [], False, 0, "no sub-daily overlap")
    best_lag, best_corr = max(curve, key=lambda lc: lc[1])
    identifiable = best_corr >= MIN_IDENTIFIABLE_CORR
    applied = best_lag if identifiable else 0
    if not identifiable:
        note = f"not identifiable (peak Δ-corr {best_corr:.2f}); held contemporaneous"
    elif best_lag > 0:
        note = f"upstream — rise reaches target ~{best_lag} h later"
    elif best_lag < 0:
        note = f"downstream — target leads it by ~{-best_lag} h"
    else:
        note = "co-located / sub-hourly travel"
    return LagResult(site, best_lag, best_corr, curve, identifiable, applied, note)


def aligned_columns(
    sites: list[str],
    hourly: dict[str, dict[int, float]],
    lags_h: dict[str, int],
    hours: list[int],
) -> list[np.ndarray]:
    """Predictor columns for ``hours``, each shifted by its per-site lag.

    ``target[h] ~ Σ βᵢ predᵢ[h - τᵢ]`` — so for hour ``h`` predictor ``i`` is
    read at ``h - τᵢ`` (τ in hours). Callers must pass only hours where every
    shifted lookup exists (see ``common_hours``).
    """
    out: list[np.ndarray] = []
    for site in sites:
        shift = lags_h[site] * SECONDS_PER_HOUR
        out.append(np.array([hourly[site][h - shift] for h in hours]))
    return out


def common_hours(
    target_h: dict[int, float],
    predictors: list[str],
    hourly: dict[str, dict[int, float]],
    lags_h: dict[str, int],
) -> list[int]:
    """Hours where the target, every contemporaneous predictor, AND every
    lag-shifted predictor all exist — the shared hold-out so contemporaneous
    and lag-aligned RMSE are computed over an identical set of points."""
    hours = []
    for h in target_h:
        ok = True
        for site in predictors:
            shift = lags_h[site] * SECONDS_PER_HOUR
            if h not in hourly[site] or (h - shift) not in hourly[site]:
                ok = False
                break
        if ok:
            hours.append(h)
    return sorted(hours)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _render_ccf_svg(slug: str, lag_results: list[LagResult], labels: dict[str, str]) -> str:
    """Lag (h) on x, first-difference correlation on y; one line per
    predictor with a marker at its peak. Standalone (img src)."""
    palette = ["#1b5591", "#c0392b", "#27ae60", "#8e44ad", "#d35400", "#16a085"]
    all_lags = sorted({lag for r in lag_results for lag, _ in r.curve})
    all_corr = [c for r in lag_results for _, c in r.curve]
    if not all_lags or not all_corr:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>\n'
    x_lo, x_hi = all_lags[0], all_lags[-1]
    y_lo, y_hi = min(0.0, min(all_corr)), max(all_corr)
    y_hi = math.ceil(y_hi * 10) / 10
    y_lo = math.floor(y_lo * 10) / 10
    # Guard degenerate ranges (single lag, or every correlation exactly 0) so
    # the px transforms never divide by zero.
    if x_hi <= x_lo:
        x_hi = x_lo + 1
    if y_hi <= y_lo:
        y_hi = y_lo + 0.1

    w, h = 640, 400
    ml, mr, mt, mb = 60, 130, 40, 50
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
    # Axis ticks.
    x_step = 6 if (x_hi - x_lo) > 24 else 3
    x = math.ceil(x_lo / x_step) * x_step
    while x <= x_hi:
        px = xpx(x)
        p.append(
            f'<line x1="{px:.1f}" y1="{mt + ph}" x2="{px:.1f}" y2="{mt + ph + 5}" stroke="#999"/>'
        )
        p.append(f'<text x="{px:.1f}" y="{mt + ph + 18}" text-anchor="middle">{x:+d}</text>')
        x += x_step
    yt = y_lo
    while yt <= y_hi + 1e-9:
        py = ypx(yt)
        p.append(f'<line x1="{ml - 5}" y1="{py:.1f}" x2="{ml}" y2="{py:.1f}" stroke="#999"/>')
        p.append(f'<text x="{ml - 8}" y="{py + 4:.1f}" text-anchor="end">{yt:.1f}</text>')
        yt += 0.2
    # Zero-lag reference.
    zx = xpx(0)
    p.append(
        f'<line x1="{zx:.1f}" y1="{mt}" x2="{zx:.1f}" y2="{mt + ph}" '
        'stroke="#333" stroke-width="1" stroke-dasharray="2,2"/>'
    )
    # One polyline + peak marker + legend entry per predictor. Unidentifiable
    # predictors (flat CCF, held contemporaneous) are dashed with no marker.
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
        tag = f"{r.applied_lag_h:+d} h" if r.identifiable else "n/a"
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


def render_markdown(
    *,
    name: str,
    daily_doc: str,
    target: str,
    predictors: list[str],
    labels: dict[str, str],
    start: str,
    end: str,
    n_hours: int,
    lag_results: list[LagResult],
    rows: list[dict],
    storm: dict,
    daily_full_rmse: float,
    daily_full_r2: float,
    daily_full_n: int,
    daily_window: tuple[str, str],
) -> str:
    """Assemble the lead/lag analysis report."""
    by_site = {r.site: r for r in lag_results}
    # Some commentary (the 5th-predictor note, the "why bounded" reasoning) is
    # specific to the McKenzie Bridge reach; gate it so a generic --target run
    # doesn't emit reach-specific claims that don't apply.
    is_mckenzie = target == DEFAULT_TARGET
    L: list[str] = []
    a = L.append

    a(f"# Sub-daily lead/lag: USGS {target} regression\n")
    a(
        "Companion to "
        "[`gauge_pair_linear.py`](../../scripts/regression/gauge_pair_linear.py) and the "
        f"daily-mean fit in [`{daily_doc}.md`](./{daily_doc}.md). "
        "**Question:** the daily-mean coefficients are applied in production to the "
        "*latest instantaneous* predictor readings — does correcting for the 1-12 h "
        "travel time between gauges measurably improve accuracy?\n"
    )
    a(f"![CCF vs lag](./{name}.svg)\n")

    cmd = " \\\n    ".join(
        ["python3 scripts/regression/gauge_lead_lag.py"]
        + [f"--predictor {s}" for s in predictors]
        + [f"--target {target}", f"--start {start}", f"--end {end}", f"--name {name}"]
    )
    a(f"Generated by:\n\n```bash\n{cmd}\n```\n")

    a("## Data\n")
    a(
        f"USGS **unit values** (sub-hourly discharge), resampled to hourly means on a "
        f"common UTC grid over **{start} → {end}** (the target's UV window). "
        f"Overlap where the target and all {len(predictors)} predictors have an hourly "
        f"value: **{n_hours:,} hours** (~{n_hours / 8766:.1f} years).\n"
    )
    a("| Role | Gauge | Label |")
    a("|---|---|---|")
    a(f"| target | `{target}` | {labels.get(target, '')} |")
    for s in predictors:
        a(f"| predictor | `{s}` | {labels.get(s, '')} |")
    a("")
    if is_mckenzie:
        a(
            "> Note: the deployed daily fit uses **5** predictors; SF Cougar `14159200` "
            "is excluded here (and from the default predictor list) because its "
            "unit-value record starts in 2000, after the target retired (1994). The "
            "daily reference below is therefore refit on the same 4 predictors for an "
            "apples-to-apples comparison.\n"
        )

    a("## Estimated travel-time lags\n")
    a(
        "Per predictor, the lag τ maximizing the correlation of hourly *first "
        "differences* (flow changes) with the target — i.e. how long a storm rise/fall "
        "takes to propagate between the two gauges. **+τ** = the predictor leads the "
        "target (upstream); **-τ** = it lags (downstream). A predictor whose CCF peak "
        f"stays below **{MIN_IDENTIFIABLE_CORR:.2f}** has no resolvable travel time and "
        "is **held contemporaneous** (applied τ = 0) so its noise can't pollute the "
        "alignment.\n"
    )
    a("| Predictor | peak τ (h) | peak Δ-corr | applied τ (h) | interpretation |")
    a("|---|---|---|---|---|")
    for s in predictors:
        r = by_site[s]
        a(
            f"| {labels.get(s, s)} `{s}` | {r.best_lag_h:+d} | {r.best_corr:.3f} | "
            f"**{r.applied_lag_h:+d}** | {r.travel_note} |"
        )
    a("")

    a("## Accuracy: contemporaneous vs travel-time-aligned\n")
    a(
        "Both alignments are evaluated on the **same** hourly hold-out grid (the only "
        "difference is whether each predictor is read at the current hour or shifted by "
        "its τ above), under two coefficient sources:\n\n"
        "* **daily-trained** — coefficients refit on daily means, the deployed style; "
        "this is the production-relevant row.\n"
        "* **hourly-refit** — coefficients refit on the hourly grid itself; the ceiling "
        "on what alignment can buy.\n"
    )
    a("| Coefficients | Alignment | n (hours) | r² | RMSE (cfs) |")
    a("|---|---|---|---|---|")
    for row in rows:
        a(
            f"| {row['coefs']} | {row['alignment']} | {row['n']:,} | "
            f"{row['r2']:.4f} | {row['rmse']:.1f} |"
        )
    a("")

    # Headline deltas.
    dt_con = next(r for r in rows if r["coefs"] == "daily-trained" and r["align_key"] == "con")
    dt_lag = next(r for r in rows if r["coefs"] == "daily-trained" and r["align_key"] == "lag")
    hr_con = next(r for r in rows if r["coefs"] == "hourly-refit" and r["align_key"] == "con")
    hr_lag = next(r for r in rows if r["coefs"] == "hourly-refit" and r["align_key"] == "lag")
    prod_gain = _pct(dt_con["rmse"], dt_lag["rmse"])
    ceil_gain = _pct(hr_con["rmse"], hr_lag["rmse"])

    a("### What the numbers say\n")
    a(
        f"- **Production-relevant (daily-trained coefficients):** travel-time alignment "
        f"moves hourly RMSE from **{dt_con['rmse']:.1f}** → **{dt_lag['rmse']:.1f} cfs** "
        f"(**{prod_gain:+.1f}%**) and r² {dt_con['r2']:.4f} → {dt_lag['r2']:.4f}.\n"
        f"- **Ceiling (hourly-refit coefficients):** **{hr_con['rmse']:.1f}** → "
        f"**{hr_lag['rmse']:.1f} cfs** ({ceil_gain:+.1f}%). Refitting on hourly data and "
        f"aligning together is the most accuracy available from this predictor set.\n"
        f"- **Daily-mean reference (same 4 predictors, {daily_window[0]}→{daily_window[1]}, "
        f"n={daily_full_n:,}):** RMSE **{daily_full_rmse:.1f} cfs**, r² {daily_full_r2:.4f}. "
        "Daily means are intrinsically smoother than instantaneous values, so this sits "
        "below the hourly RMSEs — it is *not* directly comparable to them, only a "
        "reference for the deployed product's daily accuracy.\n"
    )

    storm_gain = _pct(storm["con_rmse"], storm["lag_rmse"])
    storm_pct = storm["pct"]
    a("### During rapid flow changes (storm rises/falls)\n")
    a(
        "Travel-time misalignment should hurt most when flow is *changing* fast — most "
        "hours are slowly-varying regulated baseflow where a 1-3 h shift barely moves the "
        f"value. Restricting to the **most rapidly changing {storm_pct:.0f}% of hours** "
        f"(|Δtarget| ≥ {storm['thresh']:.0f} cfs/h — the threshold is the 90th percentile "
        "of |Δ|, but discrete USGS values tie at it so the subset is wider than a tenth; "
        f"n = {storm['n']:,} hours), with the daily-trained coefficients:\n"
    )
    a("| Subset | Alignment | n | r² | RMSE (cfs) |")
    a("|---|---|---|---|---|")
    a(
        f"| fastest-changing {storm_pct:.0f}% | contemporaneous | {storm['n']:,} | "
        f"{storm['con_r2']:.4f} | {storm['con_rmse']:.1f} |"
    )
    a(
        f"| fastest-changing {storm_pct:.0f}% | travel-time-aligned | {storm['n']:,} | "
        f"{storm['lag_r2']:.4f} | {storm['lag_rmse']:.1f} |"
    )
    a("")
    a(
        f"Alignment changes storm-subset RMSE by **{storm_gain:+.1f}%** "
        f"({storm['con_rmse']:.1f} → {storm['lag_rmse']:.1f} cfs). "
        + (
            "So the lags carry usable signal exactly where the physics predicts — it is "
            "just diluted to near-zero across the mostly-flat full record.\n"
            if storm_gain > 1.0
            else "So even where misalignment should bite hardest the lags buy little: at "
            "this reach's short travel times and heavily regulated, slowly-varying flow, "
            "sub-daily alignment carries essentially no usable signal.\n"
        )
    )

    # Headline = the production-style aggregate, but flag if storms tell a
    # different story so the verdict isn't misleadingly flat.
    verdict_material = prod_gain >= 5.0 or storm_gain >= 10.0
    a("## Verdict & recommendation\n")
    if verdict_material:
        a(
            f"Travel-time alignment is **worth pursuing** here: {prod_gain:+.1f}% RMSE "
            f"overall and {storm_gain:+.1f}% on the fastest-changing hours (production-"
            "style coefficients). The deployable, upstream-only share is the part to wire "
            "in (see below).\n"
        )
    else:
        a(
            f"Travel-time alignment yields a **negligible** gain here: {prod_gain:+.1f}% "
            f"RMSE overall and {storm_gain:+.1f}% even on the fastest-changing "
            f"{storm_pct:.0f}% of hours (production-style coefficients), both well inside "
            "the residual scatter. **Recommendation: do not wire lead/lag into this "
            "reach's estimate** — the complexity (below) buys nothing measurable. Keep "
            "using contemporaneous latest readings.\n"
        )
    if is_mckenzie:
        a(
            "**Why the effect is bounded for this reach:** the dominant term is Trail "
            "Bridge (coefficient ≈ 1.21), only ~7 river miles upstream, so its lead is "
            "just a few hours; the smaller-coefficient tributaries contribute little even "
            "when mis-aligned. The downstream term (Vida) would need *future* readings to "
            "align perfectly, which a real-time estimate cannot have — so its share of "
            "the gain is **not deployable** (see below).\n"
        )
    else:
        a(
            "**Why the effect is bounded:** it scales with the predictors' travel times "
            "(short hops barely move at the hourly scale, especially on regulated, "
            "slowly-varying flow) and their coefficients. Any downstream predictor "
            "(negative τ) would need *future* readings to align perfectly — its share is "
            "**not deployable** for a real-time nowcast (see below).\n"
        )

    a("### Deployability (what it *would* take — not recommended for this reach)\n")
    a(
        "Recorded for completeness and for reaches where the gain is larger. Applying "
        "lags in production is **not** a coefficient change; it requires the calculator "
        "to read each predictor's value *from τ hours ago* rather than its latest:\n\n"
        "1. **Upstream predictors (+τ):** deployable — the needed value is in the past, "
        "already in the `observation` table. The calculator would select the reading "
        "closest to `now - τ` instead of `LatestObservation`.\n"
        "2. **Downstream predictors (-τ):** **not** deployable for a *nowcast* — the "
        "best-aligned value is in the future. Leave them contemporaneous (forfeiting "
        "their share) or treat the estimate as a short forecast.\n"
        "3. **Plumbing:** `calc_expression` currently references only "
        "`LatestObservation`; a lag-aware estimate needs a new time-offset reference "
        "form (e.g. `tb::…::flow@-2h`) and a windowed lookup in `kayak.cli.calculator`. "
        "A real feature — justified only when the **upstream-only, deployable** share of "
        "the gain is large enough to matter, which it is not for this reach.\n"
    )

    a("## Method\n")
    a(
        "- **Unit values** pulled unfiltered from `nwis.waterservices.usgs.gov` (the only "
        "host serving pre-2007 UV) and resampled to hourly means; 15-min (Trail Bridge) "
        "and 30-min sites land on the same grid.\n"
        "- **Lag estimation** maximizes the correlation of hourly first differences "
        "(flow *changes* propagate; baseline levels are near-identical across "
        "neighbours and would pin the peak at τ≈0).\n"
        "- **Fair comparison:** contemporaneous and aligned RMSE use one shared "
        "hold-out grid — the hours where every contemporaneous *and* every shifted "
        "predictor value exists — so only alignment varies.\n"
        f"- **Caveat:** the hourly hold-out ({start}..{end}, ~{n_hours / 8766:.1f} yr of "
        "overlap) is far shorter than the daily fit's multi-decade record"
        + (" and excludes SF Cougar" if is_mckenzie else "")
        + "; the daily-reference row controls for the predictor-set change but not the "
        "window.\n"
    )
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
# Defaults reproduce the McKenzie Bridge analysis. SF Cougar (14159200) is
# absent from the predictors because its unit-value record starts in 2000.
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Sub-daily lead/lag impact on a gauge regression.")
    ap.add_argument("--target", default=DEFAULT_TARGET)
    ap.add_argument("--predictor", action="append", dest="predictors")
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=DEFAULT_END)
    ap.add_argument("--name", default=DEFAULT_NAME)
    ap.add_argument(
        "--daily-doc",
        default=DEFAULT_DAILY_DOC,
        help="Slug of the sibling daily-mean report to cross-link (no extension).",
    )
    ap.add_argument("--max-lag", type=int, default=18, help="Search ±this many hours.")
    ap.add_argument("--daily-start", default="1968-10-01", help="Daily-reference window start.")
    ap.add_argument("--out", type=Path, help="Markdown output path (also writes .svg).")
    args = ap.parse_args()
    predictors: list[str] = args.predictors or DEFAULT_PREDICTORS
    labels = LABELS
    sites = [args.target, *predictors]
    years = range(int(args.start[:4]), int(args.end[:4]) + 1)

    print(
        f"Fetching unit values for {len(sites)} sites, {years.start}-{years.stop - 1} ...",
        file=sys.stderr,
    )
    lo, hi = _window_epochs(args.start, args.end)
    hourly: dict[str, dict[int, float]] = {}
    for s in sites:
        full = fetch_hourly(s, years)
        hourly[s] = {h: v for h, v in full.items() if lo <= h < hi}
        print(f"  {s}: {len(hourly[s]):,} hourly values", file=sys.stderr)

    target_h = hourly[args.target]
    lags_search = range(-args.max_lag, args.max_lag + 1)

    # Per-predictor travel-time lag from first-difference CCF; below the
    # identifiability floor the predictor is held contemporaneous.
    lag_results: list[LagResult] = []
    lags_h: dict[str, int] = {}
    for s in predictors:
        r = classify_lag(s, ccf_curve(target_h, hourly[s], lags_search))
        lag_results.append(r)
        lags_h[s] = r.applied_lag_h
        print(
            f"  lag {s}: peak {r.best_lag_h:+d} h (corr {r.best_corr:.3f}) "
            f"-> applied {r.applied_lag_h:+d} h",
            file=sys.stderr,
        )

    contemporaneous = dict.fromkeys(predictors, 0)

    # Shared hold-out: hours where contemporaneous AND lag-shifted values exist.
    hours = common_hours(target_h, predictors, hourly, lags_h)
    if len(hours) < 100:
        print(f"Only {len(hours)} usable hours — aborting.", file=sys.stderr)
        return 1
    y = np.array([target_h[h] for h in hours])
    cols_con = aligned_columns(predictors, hourly, contemporaneous, hours)
    cols_lag = aligned_columns(predictors, hourly, lags_h, hours)

    # Daily reference (same predictors), and daily-trained coefficients. The
    # window ends at the target's last daily observation (its record end —
    # 1994-09-29 for retired McKenzie Bridge, recent for an active target).
    daily = {s: fetch_daily_means(s) for s in sites}
    daily_end = max(daily[args.target])
    dkeys = sorted(
        set(daily[args.target]) & set[str].intersection(*(set(daily[s]) for s in predictors))
    )
    dwin = [k for k in dkeys if args.daily_start <= k <= daily_end]
    dcols = [np.array([daily[s][k] for k in dwin]) for s in predictors]
    dy = np.array([daily[args.target][k] for k in dwin])
    daily_coefs = ols(dcols, dy)
    daily_rmse, daily_r2 = eval_fit(dcols, dy, daily_coefs)

    # Hourly-refit coefficients (fit on the same hold-out, per alignment).
    coefs_con = ols(cols_con, y)
    coefs_lag = ols(cols_lag, y)

    def row(
        coef_label: str,
        align_label: str,
        align_key: str,
        cols: list[np.ndarray],
        coefs: np.ndarray,
    ) -> dict:
        rmse, r2 = eval_fit(cols, y, coefs)
        return {
            "coefs": coef_label,
            "alignment": align_label,
            "align_key": align_key,
            "n": len(hours),
            "rmse": rmse,
            "r2": r2,
        }

    rows = [
        row("daily-trained", "contemporaneous (lag 0)", "con", cols_con, daily_coefs),
        row("daily-trained", "travel-time-aligned", "lag", cols_lag, daily_coefs),
        row("hourly-refit", "contemporaneous (lag 0)", "con", cols_con, coefs_con),
        row("hourly-refit", "travel-time-aligned", "lag", cols_lag, coefs_lag),
    ]

    # Storm-rise subset: hours where the target is changing fastest, where a
    # travel-time misalignment should bite hardest. If alignment helps anywhere
    # it should be here; if it doesn't even here, the lags carry no usable
    # signal at this resolution.
    prev = np.array(
        [
            target_h[h] - target_h[h - SECONDS_PER_HOUR]
            if (h - SECONDS_PER_HOUR) in target_h
            else np.nan
            for h in hours
        ]
    )
    valid = ~np.isnan(prev)
    thresh = float(np.percentile(np.abs(prev[valid]), 90))
    storm_mask = valid & (np.abs(prev) >= thresh)
    s_con_rmse, s_con_r2 = eval_fit([c[storm_mask] for c in cols_con], y[storm_mask], daily_coefs)
    s_lag_rmse, s_lag_r2 = eval_fit([c[storm_mask] for c in cols_lag], y[storm_mask], daily_coefs)
    storm = {
        "n": int(storm_mask.sum()),
        # Actual fraction of hours selected. With a 90th-percentile threshold
        # this is ~10% in continuous data, but discrete USGS values tie at the
        # threshold, so report what `>=` actually captured rather than "10%".
        "pct": 100.0 * int(storm_mask.sum()) / int(valid.sum()),
        "thresh": thresh,
        "con_rmse": s_con_rmse,
        "con_r2": s_con_r2,
        "lag_rmse": s_lag_rmse,
        "lag_r2": s_lag_r2,
    }
    print(
        f"  storm subset n={storm['n']} (|Δtarget|>={thresh:.0f} cfs/h): "
        f"con {s_con_rmse:.1f} -> lag {s_lag_rmse:.1f} cfs",
        file=sys.stderr,
    )

    md = render_markdown(
        name=args.name,
        daily_doc=args.daily_doc,
        target=args.target,
        predictors=predictors,
        labels=labels,
        start=args.start,
        end=args.end,
        n_hours=len(hours),
        lag_results=lag_results,
        rows=rows,
        storm=storm,
        daily_full_rmse=daily_rmse,
        daily_full_r2=daily_r2,
        daily_full_n=len(dwin),
        daily_window=(args.daily_start, daily_end),
    )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md)
        args.out.with_suffix(".svg").write_text(_render_ccf_svg(args.name, lag_results, labels))
        print(f"Wrote {args.out} and {args.out.with_suffix('.svg')}", file=sys.stderr)
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
