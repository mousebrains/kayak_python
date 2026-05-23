#!/usr/bin/env python3
"""Phase 2B: compute max_gradient and the continuous gradient profile.

Reads the per-reach elevation cache produced by sample_reach_elevations.py,
applies a light rolling-mean smoothing, then derives:

  * ``max_gradient`` — the steepest 1-mile drop (ft/mi). Fixed window
    width to keep the value comparable across reaches.

  * ``gradient_profile`` — a JSON document with the continuous gradient
    sampled every 0.05 mi along the reach. For each output point, the
    *smallest* window in {0.25, 0.5, 1.0, 2.0, 5.0} mi whose drop
    exceeds the 3 sigma noise floor is selected; if even 5 mi doesn't reach
    it, the value is reported anyway with ``significant: false``.

The noise floor is `3 · √2 · rmse_m` converted to feet — the minimum
drop two independent DEM samples can be distinguished by at 99.7%.
With the 1/3 arc-second default RMSE of 2.4 m, that's ≈ 33.5 ft.

Idempotent: dry-run by default. ``--apply`` writes back to the DB.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = os.environ.get("KAYAK_DB", "")
DEFAULT_CACHE = Path("Elevation-cache")
M_TO_FT = 3.28083989501

# Adaptive window set. 0.0625 mi ≈ 100m, only meaningful with LIDAR-level
# vertical accuracy + dense (≤ 25 m) sample interval. 1/3 arc-second reaches
# will naturally skip past 0.0625/0.125 because the 33 ft 3-sigma threshold
# requires steeper-than-real gradients to qualify at those sizes; they fall
# through to 0.25+.
WINDOW_SET_MI = [0.0625, 0.125, 0.25, 0.5, 1.0, 2.0, 5.0]

# Per-source vertical RMSE in meters, used for the 3-sigma significance test.
# Each window's drop is compared against 3 * sqrt(sigma_lo^2 + sigma_hi^2)
# converted to feet, where sigma_lo and sigma_hi come from the endpoint
# samples' DEM source. This replaces the global --rmse-m hand-tune so a
# mixed-source reach (some 1m LIDAR, some 1/3 arc-second) gets the
# appropriate threshold per window without per-reach config.
SRC_RMSE_M = {
    "1arc3": 2.4,    # USGS 3DEP 1/3 arc-second seamless
    "1m": 0.15,      # OPR DEM 1 meter (typical Pacific NW project accuracy)
}


def _smooth(values: list[float], window_points: int) -> list[float]:
    """Centered rolling-mean smoothing. Edge handling: mean over the
    in-range window only (so the first/last point's smoothed value is the
    mean of fewer points). Preserves array length."""
    if window_points <= 1 or len(values) < 2:
        return list(values)
    half = window_points // 2
    n = len(values)
    out = [0.0] * n
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        window = values[lo:hi]
        out[i] = sum(window) / len(window)
    return out


def _index_at_or_after(d_mi: list[float], target: float, lo: int = 0) -> int:
    """Binary-ish linear search: return smallest i with d_mi[i] >= target."""
    n = len(d_mi)
    if target <= d_mi[lo]:
        return lo
    if target >= d_mi[-1]:
        return n - 1
    # Profile is monotonically increasing in d_mi; do a simple bisect.
    lo_i, hi_i = lo, n - 1
    while lo_i < hi_i:
        mid = (lo_i + hi_i) // 2
        if d_mi[mid] < target:
            lo_i = mid + 1
        else:
            hi_i = mid
    return lo_i


def _drop_over_window(
    d_mi: list[float], elev_ft: list[float], x: float, w_mi: float
) -> tuple[float, float, int, int] | None:
    """Return (drop_ft, actual_w_mi, i_lo, i_hi) for a window centered on x
    of nominal width w_mi. Returns None if the window doesn't fit within
    the reach (clamped to ends instead — see below)."""
    half = w_mi / 2.0
    lo_target = x - half
    hi_target = x + half
    if hi_target > d_mi[-1] or lo_target < d_mi[0]:
        # Shift the window so it fits, preserving width if possible.
        if lo_target < d_mi[0] and hi_target > d_mi[-1]:
            # Window is wider than the reach — span the whole thing.
            i_lo, i_hi = 0, len(d_mi) - 1
        elif lo_target < d_mi[0]:
            i_lo = 0
            i_hi = _index_at_or_after(d_mi, d_mi[0] + w_mi)
        else:
            i_hi = len(d_mi) - 1
            target = d_mi[-1] - w_mi
            i_lo = _index_at_or_after(d_mi, target)
    else:
        i_lo = _index_at_or_after(d_mi, lo_target)
        i_hi = _index_at_or_after(d_mi, hi_target)
    actual_w = d_mi[i_hi] - d_mi[i_lo]
    if actual_w <= 0:
        return None
    drop = elev_ft[i_lo] - elev_ft[i_hi]  # signed: positive when descending downstream
    return drop, actual_w, i_lo, i_hi


def compute_max_gradient(d_mi: list[float], elev_ft: list[float], window_mi: float) -> float | None:
    """Slide a fixed-width window over the profile, return max descending
    drop / window. Rivers flow downhill; a windowed segment whose
    end-elevation is *higher* than its start is a DEM/canopy artifact,
    not a real upstream-pointing rapid — clamp those to 0 so they don't
    inflate max_gradient as |drop|."""
    if len(d_mi) < 2 or d_mi[-1] < window_mi:
        # Reach shorter than window — fall back to total drop / length
        if len(d_mi) < 2:
            return None
        total_drop = max(0.0, elev_ft[0] - elev_ft[-1])
        return round(total_drop / d_mi[-1], 1)
    best = 0.0
    i_lo = 0
    while i_lo < len(d_mi):
        target_hi = d_mi[i_lo] + window_mi
        if target_hi > d_mi[-1]:
            break
        i_hi = _index_at_or_after(d_mi, target_hi, lo=i_lo)
        actual_w = d_mi[i_hi] - d_mi[i_lo]
        if actual_w <= 0:
            i_lo += 1
            continue
        drop = max(0.0, elev_ft[i_lo] - elev_ft[i_hi])
        grad = drop / actual_w
        if grad > best:
            best = grad
        i_lo += 1
    return round(best, 1)


def _min_drop_ft_for_endpoints(
    srcs: list[str], i_lo: int, i_hi: int, default_rmse_m: float
) -> float:
    """3-sigma drop threshold (ft) for a window spanning samples i_lo to i_hi.

    Uses the endpoint samples' DEM source to pick the per-point sigma — a
    LIDAR-to-LIDAR window has ~14 cm noise, while a 1arc3 endpoint pulls
    the threshold up to the 1arc3 dominated value regardless of the other
    endpoint. (DEM error compounds as sqrt(sum of squares).)"""
    sigma_lo = SRC_RMSE_M.get(srcs[i_lo], default_rmse_m) if srcs else default_rmse_m
    sigma_hi = SRC_RMSE_M.get(srcs[i_hi], default_rmse_m) if srcs else default_rmse_m
    sigma_diff_m = math.sqrt(sigma_lo**2 + sigma_hi**2)
    return 3.0 * sigma_diff_m * M_TO_FT


def build_profile(
    d_mi: list[float],
    elev_ft: list[float],
    lat: list[float],
    lon: list[float],
    srcs: list[str],
    step_mi: float,
    window_set: list[float],
    default_rmse_m: float,
) -> list[dict]:
    """For each output point at step_mi spacing, find the smallest window in
    `window_set` whose descending drop >= per-window 3-sigma threshold and
    emit the gradient.

    The threshold is computed per-window from the endpoint samples' src
    tags via SRC_RMSE_M — so a LIDAR-sampled reach picks much finer windows
    than a 1arc3-sampled one without per-reach config.

    Gradient is clamped at 0 — an upstream-pointing windowed slope is a
    DEM/canopy artifact (rivers flow downhill), not a real feature."""
    samples = []
    total_mi = d_mi[-1]
    x = 0.0
    while x <= total_mi + 1e-9:
        chosen = None
        for w in window_set:
            res = _drop_over_window(d_mi, elev_ft, x, w)
            if res is None:
                continue
            drop, actual_w, i_lo, i_hi = res
            min_drop_ft = _min_drop_ft_for_endpoints(srcs, i_lo, i_hi, default_rmse_m)
            # Only descending drops count toward significance — uphill
            # noise of similar magnitude doesn't reflect channel reality.
            if drop >= min_drop_ft:
                chosen = (drop, actual_w, w, True)
                break
        if chosen is None:
            # Use the maximum window even if not significant
            res = _drop_over_window(d_mi, elev_ft, x, window_set[-1])
            if res is None:
                x += step_mi
                continue
            drop, actual_w, _, _ = res
            chosen = (drop, actual_w, window_set[-1], False)

        drop, actual_w, _nominal_w, significant = chosen
        grad = max(0.0, drop / actual_w) if actual_w > 0 else 0.0

        # Per-sample lat/lon: pick the nearest cached point
        i_nearest = _index_at_or_after(d_mi, x)
        samples.append(
            {
                "d_mi": round(x, 4),
                "lat": round(lat[i_nearest], 6),
                "lon": round(lon[i_nearest], 6),
                "grad_ft_per_mi": round(grad, 1),
                "w_mi": round(actual_w, 4),
                "significant": bool(significant),
            }
        )
        x += step_mi
    return samples


def process_cache_file(
    cache_path: Path,
    args,
) -> tuple[int, float | None, dict | None]:
    """Process one cache file. Returns (reach_id, max_gradient, profile)."""
    with open(cache_path) as fh:
        cache = json.load(fh)
    pts = cache.get("points", [])
    if len(pts) < 2:
        return cache["reach_id"], None, None

    d_mi = [p["d_mi"] for p in pts]
    lat = [p["lat"] for p in pts]
    lon = [p["lon"] for p in pts]
    elev_ft = [p["elev_ft"] for p in pts]
    srcs = [p.get("src", "1arc3") for p in pts]

    smoothed = _smooth(elev_ft, args.smooth_points)

    max_grad = compute_max_gradient(d_mi, smoothed, args.max_window_mi)

    samples = build_profile(
        d_mi,
        smoothed,
        lat,
        lon,
        srcs,
        step_mi=args.profile_step_mi,
        window_set=WINDOW_SET_MI,
        default_rmse_m=args.rmse_m,
    )
    # Report the source mix in the profile JSON so the renderer (or
    # debugging humans) can see which DEM tier drove the threshold picks.
    src_hist: dict[str, int] = {}
    for s in srcs:
        src_hist[s] = src_hist.get(s, 0) + 1
    profile = {
        "step_mi": args.profile_step_mi,
        "default_rmse_m": args.rmse_m,
        "src_rmse_m": SRC_RMSE_M,
        "src_histogram": src_hist,
        "samples": samples,
    }
    return cache["reach_id"], max_grad, profile


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--cache-dir", default=str(DEFAULT_CACHE), type=Path)
    ap.add_argument("--reach-ids", help="Comma-separated reach IDs (default: all with caches)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--max-window-mi", type=float, default=1.0, help="Window for max_gradient (default 1.0)"
    )
    ap.add_argument(
        "--profile-step-mi", type=float, default=0.05, help="Profile sample density (default 0.05)"
    )
    ap.add_argument(
        "--rmse-m",
        type=float,
        default=2.4,
        help="Default vertical RMSE in meters used when a sample's 'src' tag "
        "is unrecognized (default 2.4 = 3DEP 1/3 arc-second). Per-source RMSE "
        "lives in SRC_RMSE_M and is applied automatically based on each "
        "sample's source tag.",
    )
    ap.add_argument(
        "--smooth-points", type=int, default=5, help="Rolling-mean window for elevation smoothing"
    )
    args = ap.parse_args()
    if not args.db:
        sys.exit("error: pass --db /path/to/kayak.db or set KAYAK_DB in env")

    if args.reach_ids:
        wanted_ids = set(int(x) for x in args.reach_ids.split(","))
        cache_files = [
            args.cache_dir / f"reach_{rid}.json"
            for rid in sorted(wanted_ids)
            if (args.cache_dir / f"reach_{rid}.json").exists()
        ]
    else:
        cache_files = sorted(args.cache_dir.glob("reach_*.json"))

    print(f"Scope: {len(cache_files)} cache file(s)")
    if not cache_files:
        return 0

    print("Per-source 3-sigma noise floor (ft):")
    for src, sigma_m in SRC_RMSE_M.items():
        sd_m = math.sqrt(2.0) * sigma_m
        print(f"  {src}: rmse={sigma_m} m, threshold={3 * sd_m * M_TO_FT:.1f} ft")
    print(f"  fallback (--rmse-m): {args.rmse_m} m")
    print()

    # Connect to DB to compare old vs. new
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    updates: list[tuple[float | None, str | None, int]] = []
    print(f"{'rid':>5}  {'name':<22}  {'old_max':>8} -> {'new_max':>8}  {'sig_frac':>8}")
    print("-" * 70)

    for cache_path in cache_files:
        rid, max_grad, profile = process_cache_file(cache_path, args)
        if profile is None:
            continue
        row = conn.execute(
            "SELECT display_name, max_gradient FROM reach WHERE id = ?", (rid,)
        ).fetchone()
        if row is None:
            continue
        old_max = row["max_gradient"]
        sig_frac = (
            sum(1 for s in profile["samples"] if s["significant"]) / len(profile["samples"])
            if profile["samples"]
            else 0.0
        )
        old_max_str = f"{old_max:.1f}" if old_max is not None else "-"
        new_max_str = f"{max_grad:.1f}" if max_grad is not None else "-"
        print(
            f"{rid:>5}  {(row['display_name'] or '')[:22]:<22}  "
            f"{old_max_str:>8} -> {new_max_str:>8}  {sig_frac:>7.1%}"
        )
        updates.append((max_grad, json.dumps(profile, separators=(",", ":")), rid))

    print()
    print(f"{len(updates)} reach(es) to update.")
    if not args.apply:
        print("Dry-run only. Pass --apply to write changes.")
        return 0

    cur = conn.cursor()
    cur.executemany(
        """
        UPDATE reach
        SET max_gradient = ?, gradient_profile = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        updates,
    )
    conn.commit()
    print(f"Applied {cur.rowcount} update(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
