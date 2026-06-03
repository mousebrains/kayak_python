"""Shared uncertainty utilities for the regression error analysis.

Daily streamflow residuals are strongly autocorrelated (lag-1 ≈ 0.7 daily,
≈ 0.97 hourly), so the nominal sample size vastly overstates the independent
information and ordinary OLS / IID-bootstrap standard errors are optimistic.
The honest tool is a **block** bootstrap: resample whole contiguous calendar
blocks (weeks / months) so autocorrelated runs move together, and the spread
across resamples reflects the *effective* sample size rather than n.

Used by both report generators (`gauge_pair_linear.py`, `gauge_lead_lag.py`)
so they share one method. Standalone — numpy only, no kayak imports.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np


def block_bootstrap(
    block_id: np.ndarray,
    statistic: Callable[[np.ndarray], object],
    n_boot: int = 1000,
    seed: int = 0,
) -> np.ndarray:
    """Block bootstrap: resample whole blocks of observations with replacement.

    ``block_id[i]`` is the calendar block (e.g. a 7-day window or ``YYYY-MM``) of
    observation ``i``; observations in the same block always move together, so
    within-block serial correlation is preserved and the resampling reflects
    the effective (not nominal) sample size. The number of blocks drawn equals
    the number of distinct blocks, so each resample is ~the same length as the
    original.

    ``statistic(idx)`` is evaluated on the resampled row indices and may return
    a scalar or a fixed-length vector. Returns an array of shape
    ``(n_boot, *statistic_shape)`` — callers take ``.std(axis=0)`` /
    ``np.percentile(..., axis=0)`` for SEs / CIs.
    """
    block_id = np.asarray(block_id)
    uniq = np.unique(block_id)
    idx_by = {b: np.where(block_id == b)[0] for b in uniq.tolist()}
    rng = np.random.default_rng(seed)
    reps: list[object] = []
    for _ in range(n_boot):
        pick = rng.choice(uniq, size=uniq.size, replace=True)
        reps.append(statistic(np.concatenate([idx_by[b] for b in pick.tolist()])))
    return np.array(reps)


def lag1_autocorr(x: np.ndarray) -> float:
    """Lag-1 autocorrelation of a 1-D series (assumes finite, evenly spaced)."""
    x = np.asarray(x, dtype=float)
    x = x - x.mean()
    denom = float(x @ x)
    return float(x[1:] @ x[:-1] / denom) if denom > 0 else float("nan")


def ar1_variance_inflation(rho: float) -> float:
    """AR(1) variance-inflation factor (1+rho)/(1-rho) for the mean of a serially
    correlated series — an indicative multiplier for how much IID standard
    errors understate the truth. Exact for the sample mean; for regression
    slopes it is order-of-magnitude, not exact."""
    return (1.0 + rho) / (1.0 - rho) if rho < 1.0 else float("inf")


def vif(cols: list[np.ndarray]) -> list[float]:
    """Variance-inflation factor per predictor column (multicollinearity).

    ``VIF_j = 1/(1 - R²_j)`` where ``R²_j`` regresses column ``j`` on the
    other columns plus an intercept. VIF ≈ 1 means independent of the others;
    > 5 moderate, > 10 serious collinearity (the individual coefficient is
    then imprecise and not safely interpretable in isolation).
    """
    matrix = np.column_stack([np.asarray(c, dtype=float) for c in cols])
    out: list[float] = []
    for j in range(matrix.shape[1]):
        yj = matrix[:, j]
        design = np.column_stack([np.ones(len(yj)), np.delete(matrix, j, axis=1)])
        beta, *_ = np.linalg.lstsq(design, yj, rcond=None)
        resid = yj - design @ beta
        ss_tot = float(((yj - yj.mean()) ** 2).sum())
        r2 = 1.0 - float(resid @ resid) / ss_tot if ss_tot > 0 else 0.0
        out.append(1.0 / (1.0 - r2) if r2 < 1.0 else float("inf"))
    return out
