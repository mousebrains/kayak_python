"""Unit tests for scripts/regression/_resample.py (offline, deterministic)."""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "regression" / "_resample.py"


def _load() -> Any:
    name = "_resample_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _SCRIPT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_block_bootstrap_shape_and_determinism():
    rs = _load()
    block_id = np.repeat(np.arange(20), 5)  # 20 blocks of 5
    data = np.arange(100, dtype=float)

    def stat(idx):
        return float(data[idx].mean())

    a = rs.block_bootstrap(block_id, stat, n_boot=200, seed=7)
    b = rs.block_bootstrap(block_id, stat, n_boot=200, seed=7)
    c = rs.block_bootstrap(block_id, stat, n_boot=200, seed=8)
    assert a.shape == (200,)
    assert np.allclose(a, b)  # same seed -> identical
    assert not np.allclose(a, c)  # different seed -> different draws
    # The bootstrap mean of the sample mean is near the true mean.
    assert abs(a.mean() - data.mean()) < 5.0


def test_block_bootstrap_vector_statistic():
    rs = _load()
    block_id = np.repeat(np.arange(10), 4)
    m = np.random.default_rng(0).normal(size=(40, 3))

    def stat(idx):
        return m[idx].mean(axis=0)

    reps = rs.block_bootstrap(block_id, stat, n_boot=50, seed=1)
    assert reps.shape == (50, 3)


def test_block_bootstrap_preserves_blocks():
    # With perfectly correlated within-block values, every resample's mean is
    # an average of block-level constants, so its variance is far below the
    # IID expectation — the whole point of block resampling.
    rs = _load()
    block_id = np.repeat(np.arange(50), 10)
    block_level = np.repeat(np.random.default_rng(2).normal(size=50), 10)

    def stat(idx):
        return float(block_level[idx].mean())

    reps = rs.block_bootstrap(block_id, stat, n_boot=500, seed=3)
    # Resample mean stays close to the grand mean (blocks are interchangeable).
    assert abs(reps.mean() - block_level.mean()) < 0.3


def test_vif_independent_vs_collinear():
    rs = _load()
    rng = np.random.default_rng(0)
    x1 = rng.normal(size=500)
    x2 = rng.normal(size=500)  # independent of x1
    x3 = x1 + 0.01 * rng.normal(size=500)  # nearly collinear with x1
    v = rs.vif([x1, x2, x3])
    assert v[1] < 2.0  # independent column -> VIF ~ 1
    assert v[0] > 10.0 and v[2] > 10.0  # the collinear pair -> high VIF


def test_vif_single_column_is_one():
    rs = _load()
    rng = np.random.default_rng(1)
    assert abs(rs.vif([rng.normal(size=100)])[0] - 1.0) < 1e-9


def test_lag1_autocorr_and_inflation():
    rs = _load()
    # AR(1) with phi=0.8 -> lag-1 autocorr ~ 0.8.
    rng = np.random.default_rng(0)
    n = 20000
    x = np.empty(n)
    x[0] = 0.0
    for i in range(1, n):
        x[i] = 0.8 * x[i - 1] + rng.normal()
    rho = rs.lag1_autocorr(x)
    assert abs(rho - 0.8) < 0.05
    # Inflation (1+r)/(1-r) is monotone and matches the closed form.
    assert math.isclose(rs.ar1_variance_inflation(0.8), 1.8 / 0.2, rel_tol=1e-9)
