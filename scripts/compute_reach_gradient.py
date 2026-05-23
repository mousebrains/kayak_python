#!/usr/bin/env python3
"""Phase 2B: compute max_gradient and the binned gradient profile.

Reads the per-reach elevation cache produced by sample_reach_elevations.py,
applies a light rolling-mean smoothing, then derives:

  * ``max_gradient`` — the steepest 1-mile drop (ft/mi). Fixed window
    width to keep the value comparable across reaches.

  * ``gradient_profile`` — a JSON document of non-overlapping bars. The
    reach is binned into ``DL_MI`` (0.2 mi) chunks and each bin's mean
    elevation is taken; the walker then finds, for each starting bin, the
    *smallest* window of n bins (up to ``MAX_WINDOW_MI`` = 5 mi) whose
    bin-mean drop clears the per-window noise floor, emits one sample for
    that window, and advances n bins. If no window up to MAX_WINDOW_MI
    qualifies, the max-window segment is emitted with ``significant: false``.

The noise floor is statistical, not a single scalar: each bin carries
``sigma_bin = sqrt(Σ sigma_sample²) / N`` from the per-sample DEM RMSE
(``SRC_RMSE_M``: 2.4 m for 1/3 arc-second, 0.15 m for 1 m LIDAR), and a
window's drop is significant when it exceeds ``M_SIGMA`` (3) ·
sqrt(sigma_i² + sigma_j²). So a window over noisier 3DEP samples needs a
larger drop than one over LIDAR. Reaches flagged ``gradient_unreliable``
are skipped (NULL profile).

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

# Bin width for the cumsum + binning algorithm. Each bin holds the mean
# of all elevation samples whose d_mi falls within [b*DL_MI, (b+1)*DL_MI).
# With 25 m along-channel sampling, DL_MI=0.2 (~322 m) gives ~13 samples
# per bin — strong bin-mean noise reduction (sigma_bin = sigma_raw / sqrt(13),
# threshold tightens accordingly). Also caps the minimum reportable
# window at 0.2 mi so the chart never shows sub-quarter-mile spikes
# that are dominated by raw-DEM noise.
DL_MI = 0.2

# Cap on the search window for finding a significant drop. If no
# significant drop is found within MAX_WINDOW_MI of the current bin,
# we emit a non-significant sample for the max-window segment and
# advance.
MAX_WINDOW_MI = 5.0

# Per-source vertical RMSE in meters. Each bin's elevation noise is
# (per-source RMSE) / sqrt(N_per_bin). For mixed-source bins we combine
# the per-sample sigmas correctly: sigma(bin_mean) = sqrt(sum sigma_i^2) / N.
# The threshold for a significant drop between bin i and bin j is
# m_sigma * sqrt(sigma(bin_i)^2 + sigma(bin_j)^2) — a constant in n,
# since the cumsum telescopes to bin_mean[j] - bin_mean[i].
SRC_RMSE_M = {
    "1arc3": 2.4,    # USGS 3DEP 1/3 arc-second seamless
    "1m": 0.15,      # OPR DEM 1 meter (typical Pacific NW project accuracy)
}

# Significance threshold in standard deviations. 3 = 99.7% confidence.
M_SIGMA = 3.0


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


def _bin_elevations(
    d_mi: list[float],
    elev_ft: list[float],
    lat: list[float],
    lon: list[float],
    srcs: list[str],
    dl_mi: float,
    default_rmse_m: float,
) -> tuple[list[float | None], list[float | None], list[float | None], list[float | None]]:
    """Bucket the cache samples into dl_mi-wide bins and return
    (bin_means_ft, bin_sigmas_ft, bin_lats, bin_lons).

    bin_sigmas[i] is the noise on bin_means[i] computed as
    sqrt(sum(sigma_sample^2)) / N_per_bin, where sigma_sample is the
    per-source RMSE looked up via SRC_RMSE_M. None entries indicate
    bins with no samples (unusual — sample interval should be smaller
    than dl_mi)."""
    total_mi = d_mi[-1]
    n_bins = int(total_mi / dl_mi) + 1
    means: list[float | None] = []
    sigmas: list[float | None] = []
    lats: list[float | None] = []
    lons: list[float | None] = []
    for b in range(n_bins):
        bin_start = b * dl_mi
        bin_end = (b + 1) * dl_mi
        i_lo = _index_at_or_after(d_mi, bin_start)
        i_hi_exc = _index_at_or_after(d_mi, bin_end)
        if i_hi_exc > i_lo and d_mi[i_hi_exc] < bin_end + 1e-9:
            i_hi_exc += 1   # _index_at_or_after returns the last sample if past end
        if i_hi_exc <= i_lo or i_lo >= len(d_mi):
            means.append(None)
            sigmas.append(None)
            lats.append(None)
            lons.append(None)
            continue
        n = i_hi_exc - i_lo
        means.append(sum(elev_ft[i_lo:i_hi_exc]) / n)
        sigmas_m = [SRC_RMSE_M.get(srcs[k], default_rmse_m) for k in range(i_lo, i_hi_exc)]
        sigma_bin_m = math.sqrt(sum(s * s for s in sigmas_m)) / n
        sigmas.append(sigma_bin_m * M_TO_FT)
        i_mid = (i_lo + i_hi_exc - 1) // 2
        lats.append(lat[i_mid])
        lons.append(lon[i_mid])
    return means, sigmas, lats, lons


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


def build_profile(  # noqa: C901 — sequential bin walk with multiple guards; splitting fragments the loop
    d_mi: list[float],
    elev_ft: list[float],
    lat: list[float],
    lon: list[float],
    srcs: list[str],
    default_rmse_m: float,
    dl_mi: float = DL_MI,
    max_window_mi: float = MAX_WINDOW_MI,
    m_sigma: float = M_SIGMA,
) -> list[dict]:
    """Bin elevations into dl_mi-wide chunks, take the bin mean, then
    walk forward to find the smallest n such that the bin_mean drop
    from bin i to bin i+n exceeds the m_sigma threshold.

    Telescoping: CS[i+n] - CS[i] = bin_mean[i+n] - bin_mean[i], so the
    noise on the cumsum is sigma(bin_mean[i+n] - bin_mean[i]) =
    sqrt(sigma_i^2 + sigma_{i+n}^2). The threshold is constant in n
    (independent-noise assumption), letting long shallow runs and
    short steep ones both find their smallest significant window.

    Emits one sample per non-overlapping window; advances to bin i+n.
    Gradient is clamped at 0 (rivers flow downhill; uphill windows
    are DEM artifacts)."""
    means, sigmas, lats, lons = _bin_elevations(
        d_mi, elev_ft, lat, lon, srcs, dl_mi, default_rmse_m
    )
    n_bins = len(means)
    max_n = max(1, int(max_window_mi / dl_mi))

    samples: list[dict] = []
    i = 0
    while i < n_bins - 1:
        if means[i] is None:
            i += 1
            continue
        chosen_n = None
        chosen_drop = 0.0
        for n in range(1, min(max_n, n_bins - i)):
            j = i + n
            if means[j] is None:
                continue
            drop = means[i] - means[j]   # signed: positive when descending
            sigma_drop = math.sqrt((sigmas[i] or 0) ** 2 + (sigmas[j] or 0) ** 2)
            if drop >= m_sigma * sigma_drop:
                chosen_n = n
                chosen_drop = drop
                break

        significant = chosen_n is not None
        if not significant:
            # No significant drop within max_window — emit one sample
            # for the max-window segment (or up to the end of the reach).
            n = min(max_n, n_bins - 1 - i)
            j = i + n
            while j > i and means[j] is None:
                j -= 1
            if j <= i:
                break
            chosen_n = j - i
            chosen_drop = max(0.0, means[i] - means[j])

        w_mi = chosen_n * dl_mi
        d_mi_center = (i + chosen_n / 2) * dl_mi
        grad = max(0.0, chosen_drop / w_mi) if w_mi > 0 else 0.0
        center_bin = i + chosen_n // 2
        # Find a non-None lat/lon near the center
        lat_v, lon_v = lats[center_bin], lons[center_bin]
        if lat_v is None:
            for off in range(1, chosen_n):
                if center_bin - off >= 0 and lats[center_bin - off] is not None:
                    lat_v, lon_v = lats[center_bin - off], lons[center_bin - off]
                    break
                if center_bin + off < n_bins and lats[center_bin + off] is not None:
                    lat_v, lon_v = lats[center_bin + off], lons[center_bin + off]
                    break

        samples.append({
            "d_mi": round(d_mi_center, 4),
            "lat": round(lat_v, 6) if lat_v is not None else None,
            "lon": round(lon_v, 6) if lon_v is not None else None,
            "grad_ft_per_mi": round(grad, 1),
            "w_mi": round(w_mi, 4),
            "significant": bool(significant),
        })
        i += chosen_n
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

    # No upstream smoothing — bin-mean averaging in build_profile is the
    # noise reduction. max_gradient still uses lightly-smoothed elevations
    # for stability of the steepest-mile statistic.
    smoothed = _smooth(elev_ft, args.smooth_points)
    max_grad = compute_max_gradient(d_mi, smoothed, args.max_window_mi)

    samples = build_profile(
        d_mi, elev_ft, lat, lon, srcs,
        default_rmse_m=args.rmse_m,
        dl_mi=args.dl_mi,
        max_window_mi=args.profile_max_window_mi,
        m_sigma=args.m_sigma,
    )
    # Report the source mix + algorithm params so the JSON is self-describing.
    src_hist: dict[str, int] = {}
    for s in srcs:
        src_hist[s] = src_hist.get(s, 0) + 1
    profile = {
        "dl_mi": args.dl_mi,
        "max_window_mi": args.profile_max_window_mi,
        "m_sigma": args.m_sigma,
        "default_rmse_m": args.rmse_m,
        "src_rmse_m": SRC_RMSE_M,
        "src_histogram": src_hist,
        "samples": samples,
    }
    return cache["reach_id"], max_grad, profile


def main() -> int:  # noqa: C901 — sequential I/O orchestration, splitting fragments the read/process/write loop
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--cache-dir", default=str(DEFAULT_CACHE), type=Path)
    ap.add_argument("--reach-ids", help="Comma-separated reach IDs (default: all with caches)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--max-window-mi", type=float, default=1.0,
        help="Window for max_gradient sliding-window scalar (default 1.0 mi)"
    )
    ap.add_argument(
        "--dl-mi", type=float, default=DL_MI,
        help=f"Bin width for the profile builder (default {DL_MI} mi)",
    )
    ap.add_argument(
        "--profile-max-window-mi", type=float, default=MAX_WINDOW_MI,
        help=f"Cap on the search-for-significance window (default {MAX_WINDOW_MI} mi)",
    )
    ap.add_argument(
        "--m-sigma", type=float, default=M_SIGMA,
        help=f"Significance threshold in std devs (default {M_SIGMA} = 99.7%%)",
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
        "--smooth-points", type=int, default=5,
        help="Rolling-mean window for max_gradient elevation smoothing only "
        "(the profile builder uses bin-mean averaging — no rolling smoothing).",
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

    # dl=DL_MI bin holds ~4 samples at 25 m along-channel spacing;
    # bin_mean noise is RMSE_raw / sqrt(N_per_bin), drop noise is
    # sqrt(2) * bin_mean_noise. Per-bin threshold below is the
    # m_sigma * drop_noise for a pure-source bin pair at the listed N.
    n_per_bin_est = max(1, int(args.dl_mi / 0.025))
    print(f"dl={args.dl_mi} mi (~{n_per_bin_est} samples/bin at 25 m), "
          f"max search {args.profile_max_window_mi} mi, "
          f"m_sigma={args.m_sigma}")
    print(f"Per-source {args.m_sigma}-sigma drop threshold (ft) for a "
          f"pure-source bin pair at N={n_per_bin_est}:")
    for src, sigma_m in SRC_RMSE_M.items():
        sigma_bin_m = sigma_m / math.sqrt(n_per_bin_est)
        sigma_drop_m = math.sqrt(2.0) * sigma_bin_m
        print(f"  {src}: rmse={sigma_m} m, threshold={args.m_sigma * sigma_drop_m * M_TO_FT:.2f} ft")
    print(f"  fallback (--rmse-m): {args.rmse_m} m")
    print()

    # Connect to DB to compare old vs. new
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    # Suppression list: reaches whose trace-derived gradient is unreliable
    # (canyon-wall artifacts, dam/falls between endpoints, etc.). Set by
    # migration 0051 onward via the reach.gradient_unreliable column.
    suppressed = {
        r[0] for r in conn.execute(
            "SELECT id FROM reach WHERE gradient_unreliable = 1"
        ).fetchall()
    }
    if suppressed:
        print(f"Suppressed reaches (gradient_unreliable=1): {sorted(suppressed)}")
        print()

    updates: list[tuple[float | None, str | None, int]] = []
    print(f"{'rid':>5}  {'name':<22}  {'old_max':>8} -> {'new_max':>8}  {'sig_frac':>8}")
    print("-" * 70)

    for cache_path in cache_files:
        rid, max_grad, profile = process_cache_file(cache_path, args)
        if profile is None:
            continue
        if rid in suppressed:
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
