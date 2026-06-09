#!/usr/bin/env python3
"""Score the old vs new EFSF_Salmon_calc expressions against measured truth.

The EFSF calc gauge (kayak_data gauge 181, calc_expression 12) represents the
EFSF just below the Johnson Cr confluence (5349 put-in). Over 1928-43 the flow
there is directly measurable as q(13312000) + q(13313000) — the retired
EFSF-above-confluence gauge plus Johnson Cr — so both candidate expressions can
be scored against truth:

- old (uncalibrated sum):  Johnson + Stibnite
- new (calibrated):        est(13312000 | Johnson, Stibnite) + Johnson

The old sum omits the ~87 mi^2 of EFSF drainage between the Stibnite gauge
(19.3 mi^2) and the confluence, hence its large negative bias. The fitted
coefficients backing `calc_expression` 12 come from the kayak_data dataset's
regression/efsf_13312000_from_johnson_stibnite.md (same OLS refit here, on
exactly the scored days, for honesty).

Reproduce:
    python3 docs/one-offs/efsf_calc_comparison.py

Expected output (2026-06-04): old bias -113.3 / RMSE 191.3; new bias -0.0 /
RMSE 26.5 (n=5044). Quoted by the EFSF entry in the kayak_data
regression/README.md and the kayak_data calc_expression.csv row-12 note.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "regression"))
import gauge_pair_linear as g

efsf = g.fetch_daily_means("13312000")  # EFSF abv Johnson confluence (truth component)
joh = g.fetch_daily_means("13313000")  # Johnson Cr at Yellow Pine (live donor)
sti = g.fetch_daily_means("13311000")  # EFSF at Stibnite (live donor)

keys = [k for k in sorted(set(efsf) & set(joh) & set(sti)) if "1928-08-13" <= k <= "1943-07-14"]
print(f"n={len(keys)} (1928-43, all three gauges present)")

fit = g.fit_ols([[joh[k] for k in keys], [sti[k] for k in keys]], [efsf[k] for k in keys], False)
b0, bj, bs = fit.coefs
print(f"duo refit: est(13312000) = {b0:.4g} + {bj:.6g}*J + {bs:.6g}*ST  (r2={fit.r2:.4f})")

errors: dict[str, list[float]] = {"old (J+ST)": [], "new (est+J)": []}
for k in keys:
    truth = efsf[k] + joh[k]
    errors["old (J+ST)"].append((joh[k] + sti[k]) - truth)
    errors["new (est+J)"].append((b0 + bj * joh[k] + bs * sti[k] + joh[k]) - truth)

for label, errs in errors.items():
    bias = sum(errs) / len(errs)
    rmse = math.sqrt(sum(e * e for e in errs) / len(errs))
    print(f"{label:12s} bias={bias:+8.1f} cfs   RMSE={rmse:7.1f} cfs")
