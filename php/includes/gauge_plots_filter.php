<?php

declare(strict_types=1);

/**
 * Pure time-series math helpers used by gauge_plots.php — no DB, no
 * output. Each function operates on parallel arrays of (times,
 * values, [sources]) and returns transformed parallel arrays.
 *
 * Helpers keep their pre-extract `_gp_` prefix (file-private to the
 * gauge_plots cluster); the prefix doesn't clash with anything
 * outside the cluster (see Tier 5 CI-lesson note in
 * docs/done/PLAN_php_layer_split.md). Split out as part of Tier 5.GP so
 * the math is testable / readable on its own.
 */

/**
 * Lower-bound binary search in a sorted int array. Returns insertion index.
 *
 * @param list<int> $sorted
 */
function _gp_lower_bound(array $sorted, int $target): int
{
    $lo = 0;
    $hi = count($sorted);
    while ($lo < $hi) {
        $mid = ($lo + $hi) >> 1;
        if ($sorted[$mid] < $target) {
            $lo = $mid + 1;
        } else {
            $hi = $mid;
        }
    }
    return $lo;
}

/**
 * Hampel-style spike rejection on one source's time series.
 *
 * For each point, compute the local median and MAD over a ±$half_window_s
 * window of the same source. Drop the point if its deviation from the
 * local median exceeds the threshold, where the threshold is
 *   max( $k * 1.4826 * MAD,  0.25 * |median| + 0.5 )
 *
 * The MAD term (k=3, 1.4826 = robust σ-estimator) catches outliers when
 * there's natural variability in the window. The relative+floor fallback
 * catches obvious spikes against a clean (MAD≈0) signal — without it the
 * Hampel filter degenerates to "keep everything" whenever a sensor is
 * sitting still. The 0.5 unit floor avoids dropping legitimate noise on
 * near-zero stages.
 *
 * Windows with fewer than 3 points don't have enough info; the point
 * stays.
 *
 * @param list<int>   $times
 * @param list<float> $values
 * @param list<int>   $sources
 * @return array{0: list<int>, 1: list<float>, 2: list<int>}
 */
function _gp_despike_per_source(
    array $times,
    array $values,
    array $sources,
    int $half_window_s = 900,
    float $k = 3.0,
): array {
    $n = count($times);
    if ($n === 0) {
        return [$times, $values, $sources];
    }
    $by_source = [];
    foreach ($sources as $i => $sid) {
        $by_source[$sid][] = $i;
    }
    $keep = array_fill(0, $n, true);
    foreach ($by_source as $indices) {
        usort($indices, fn($a, $b) => $times[$a] <=> $times[$b]);
        $st = [];
        $sv = [];
        foreach ($indices as $i) {
            $st[] = $times[$i];
            $sv[] = $values[$i];
        }
        $m = count($st);
        for ($i = 0; $i < $m; $i++) {
            $lo = _gp_lower_bound($st, $st[$i] - $half_window_s);
            $hi = $i;
            while ($hi < $m - 1 && $st[$hi + 1] <= $st[$i] + $half_window_s) {
                $hi++;
            }
            $w = $hi - $lo + 1;
            if ($w < 3) {
                continue;
            }
            $window = array_slice($sv, $lo, $w);
            sort($window);
            $median = ($w & 1)
                ? $window[intdiv($w, 2)]
                : ($window[intdiv($w, 2) - 1] + $window[intdiv($w, 2)]) / 2.0;
            $abs_dev = [];
            foreach ($window as $v) {
                $abs_dev[] = abs($v - $median);
            }
            sort($abs_dev);
            $mad = ($w & 1)
                ? $abs_dev[intdiv($w, 2)]
                : ($abs_dev[intdiv($w, 2) - 1] + $abs_dev[intdiv($w, 2)]) / 2.0;
            $threshold = max($k * 1.4826 * $mad, 0.25 * abs($median) + 0.5);
            if (abs($sv[$i] - $median) > $threshold) {
                $keep[$indices[$i]] = false;
            }
        }
    }
    $ot = [];
    $ov = [];
    $os = [];
    for ($i = 0; $i < $n; $i++) {
        if ($keep[$i]) {
            $ot[] = $times[$i];
            $ov[] = $values[$i];
            $os[] = $sources[$i];
        }
    }
    return [$ot, $ov, $os];
}

/**
 * Source-balanced moving mean for plot data with overlapping multi-source points.
 *
 * For each unique input timestamp ``t``: collect samples in a ±$half_window_s
 * window, average within each contributing source, then mean across sources.
 * Per-source-mean-then-cross-source-mean gives every source equal weight
 * regardless of cadence — so a 5-min USGS feed and a 15-min NWS feed each
 * count once at the cross-source step instead of USGS dominating 7-to-3.
 *
 * Edge handling: when ``t`` is within $half_window_s of the latest input
 * sample, the forward half is truncated — past side stays at 15 min, future
 * side shrinks to whatever is available (one-sided at the boundary).
 *
 * @param list<int>   $times
 * @param list<float> $values
 * @param list<int>   $sources
 * @return array{0: list<int>, 1: list<float>}
 */
function _gp_cross_source_mean(
    array $times,
    array $values,
    array $sources,
    int $half_window_s = 900,
): array {
    $n = count($times);
    if ($n < 2) {
        return [$times, $values];
    }
    $idx = range(0, $n - 1);
    usort($idx, fn($a, $b) => $times[$a] <=> $times[$b]);
    $st = [];
    $sv = [];
    $ss = [];
    foreach ($idx as $i) {
        $st[] = $times[$i];
        $sv[] = $values[$i];
        $ss[] = $sources[$i];
    }
    $latest_t = $st[$n - 1];

    $out_t = [];
    $prev = null;
    foreach ($st as $t) {
        if ($t !== $prev) {
            $out_t[] = $t;
            $prev = $t;
        }
    }

    $out_v = [];
    foreach ($out_t as $t) {
        $window_start = $t - $half_window_s;
        $window_end   = ($latest_t - $t < $half_window_s) ? $t : $t + $half_window_s;
        $lo = _gp_lower_bound($st, $window_start);
        $sums = [];
        $cnts = [];
        for ($i = $lo; $i < $n && $st[$i] <= $window_end; $i++) {
            $sid = $ss[$i];
            $sums[$sid] = ($sums[$sid] ?? 0.0) + $sv[$i];
            $cnts[$sid] = ($cnts[$sid] ?? 0) + 1;
        }
        if ($sums === []) {
            continue;
        }
        $sum_of_means = 0.0;
        $n_sources = 0;
        foreach ($sums as $sum_sid => $s) {
            $sum_of_means += $s / $cnts[$sum_sid];
            $n_sources++;
        }
        $out_v[] = $sum_of_means / $n_sources;
    }
    return [$out_t, $out_v];
}
