#!/usr/bin/env python3
"""Out-of-era validation of the Coweeman calc against the WA Ecology record.

The Coweeman calc (fit vs USGS 14245000, calibration window 1950-84) can be
scored against an *independent, out-of-era* observed record: WA Ecology
station 26C075 "Coweeman R. nr Kelso" ran telemetered daily discharge from
Sep 2006 to Nov 2019. If the 1950s-era relationship to EF Lewis (14222500)
still holds 25+ years after the USGS gauge died, the stationarity assumption
behind the deployed expression is directly evidenced rather than assumed.

DOE daily files (one per water year, downloaded 2026-06-04):
    https://apps.ecology.wa.gov/ContinuousFlowAndWQ/StationData/Prod/26C075/26C075_<WY>_DSG_DV.txt

Reproduce:
    mkdir -p /tmp/doe_coweeman
    for y in $(seq 2006 2019); do
      curl -s -A Mozilla/5.0 \
        "https://apps.ecology.wa.gov/ContinuousFlowAndWQ/StationData/Prod/26C075/26C075_${y}_DSG_DV.txt" \
        -o /tmp/doe_coweeman/$y.txt
    done
    python3 docs/one-offs/coweeman_doe_validation.py

Caveat: 26C075 and USGS 14245000 are nearby but not identical sites; a small
systematic offset may be site difference, not fit drift.
"""

from __future__ import annotations

import math
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "regression"))
import gauge_pair_linear as g

DOE_DIR = Path("/tmp/doe_coweeman")
ROW = re.compile(r"^(\d{2}/\d{2}/\d{4})\s+([\d.]+)\s+(\d+)\s*$")

# The deployed Coweeman expression (quadratic solo on EF Lewis, 1950-84 fit).
B0, B1, B2 = -4.64, 0.5745, -9.559e-06


def doe_daily() -> dict[str, float]:
    out: dict[str, float] = {}
    for f in sorted(DOE_DIR.glob("*.txt")):
        for line in f.read_text().splitlines():
            m = ROW.match(line.strip())
            if m:
                d = datetime.strptime(m.group(1), "%m/%d/%Y").strftime("%Y-%m-%d")
                out[d] = float(m.group(2))
    return out


def main() -> int:
    doe = doe_daily()
    ef = g.fetch_daily_means("14222500")
    keys = sorted(set(doe) & set(ef))
    print(f"DOE 26C075 daily values: {len(doe)}; overlap with EF Lewis: {len(keys)}")
    errs = []
    for k in keys:
        est = max(0.0, B0 + B1 * ef[k] + B2 * ef[k] * ef[k])
        errs.append(est - doe[k])
    n = len(errs)
    bias = sum(errs) / n
    rmse = math.sqrt(sum(e * e for e in errs) / n)
    mean_obs = sum(doe[k] for k in keys) / n
    ss_tot = sum((doe[k] - mean_obs) ** 2 for k in keys)
    r2 = 1 - sum(e * e for e in errs) / ss_tot
    print(f"n={n}  mean(obs)={mean_obs:.1f} cfs")
    print(
        f"bias={bias:+.1f} cfs ({100 * bias / mean_obs:+.1f}% of mean)  RMSE={rmse:.1f}  r2={r2:.4f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
