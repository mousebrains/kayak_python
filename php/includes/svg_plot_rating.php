<?php

declare(strict_types=1);

/**
 * Rating-curve interpolation — gauge-height ↔ flow conversion derived
 * from paired observations.
 *
 * Extracted from svg_plot.php in Tier 4.3 of php_layer_split. The three
 * functions form a closed loop: `derive_rating_lookup` produces the
 * piecewise-linear lookup that `rate_gauge_to_flow` and
 * `rate_flow_to_gauge` consume. Independently testable; covered by 6
 * of SvgPlotTest's 11 cases.
 *
 * External consumers: php/includes/gauge_plots.php (all three functions).
 * Transitive consumer: php/includes/svg_plot.php
 * (`generate_rating_dual_plot` calls rate_*_to_* internally), so
 * svg_plot.php `require_once`s this file.
 *
 * No `require_once` of its own — the SQL takes a `PDO` parameter; no
 * other helpers are touched.
 */

/**
 * Build a piecewise-linear (gauge_ft, flow_cfs) lookup from paired observations.
 *
 * Pairs $primary_type ('flow' or 'inflow') with 'gauge' on matching
 * (source_id, observed_at). Drops primary <= 0 (tide/release zeros).
 * Bins by gauge_ft and emits (median gauge_ft, median flow) per non-empty bin,
 * sorted by gauge_ft and filtered to monotone-increasing flow so the inverse
 * flow->gauge lookup is well-defined.
 *
 * @return array<int, array{0: float, 1: float}>|null  Sorted (gauge_ft, flow_cfs) pairs, or null if < 2 bins survive.
 */
function derive_rating_lookup(
    PDO $db,
    int $gauge_id,
    string $primary_type,
    string $since,
    int $n_bins = 50
): ?array {
    if ($primary_type !== 'flow' && $primary_type !== 'inflow') return null;

    $stmt = $db->prepare(
        "SELECT g.value AS gauge_ft, p.value AS primary_val
         FROM observation p
         JOIN observation g ON g.source_id = p.source_id
                          AND g.observed_at = p.observed_at
         JOIN gauge_source gs ON gs.source_id = p.source_id
         WHERE gs.gauge_id = ?
           AND p.data_type = ?
           AND g.data_type = 'gauge'
           AND p.observed_at >= ?
           AND p.value > 0"
    );
    $stmt->execute([$gauge_id, $primary_type, $since]);

    $rows = [];
    $gmin = INF; $gmax = -INF;
    while ($r = $stmt->fetch(PDO::FETCH_ASSOC)) {
        $g = (float)$r['gauge_ft'];
        $v = (float)$r['primary_val'];
        $rows[] = [$g, $v];
        if ($g < $gmin) $gmin = $g;
        if ($g > $gmax) $gmax = $g;
    }
    if (count($rows) < 2 || $gmax - $gmin < 1e-9) return null;

    $bin_width = ($gmax - $gmin) / $n_bins;
    $bins = [];
    foreach ($rows as [$rg, $rv]) {
        $idx = min($n_bins - 1, (int)floor(($rg - $gmin) / $bin_width));
        $bins[$idx][] = [$rg, $rv];
    }

    $lookup = [];
    foreach ($bins as $bin) {
        $gs = array_column($bin, 0);
        $vs = array_column($bin, 1);
        sort($gs);
        sort($vs);
        $n = count($bin);
        $mid = intdiv($n, 2);
        $g_med = $n % 2 === 1 ? $gs[$mid] : ($gs[$mid - 1] + $gs[$mid]) / 2;
        $v_med = $n % 2 === 1 ? $vs[$mid] : ($vs[$mid - 1] + $vs[$mid]) / 2;
        $lookup[] = [$g_med, $v_med];
    }
    usort($lookup, fn($a, $b) => $a[0] <=> $b[0]);

    // Enforce monotone-increasing flow so flow->gauge inverse stays well-defined.
    $filtered = [];
    $prev_flow = -INF;
    foreach ($lookup as $pair) {
        if ($pair[1] > $prev_flow) {
            $filtered[] = $pair;
            $prev_flow = $pair[1];
        }
    }

    return count($filtered) >= 2 ? $filtered : null;
}

/**
 * Forward rating: gauge ft -> flow cfs (linear interp, clamped at endpoints).
 *
 * Mirrors src/kayak/utils/conversions.py::interpolate_rating.
 *
 * @param array<int, array{0: float, 1: float}> $lookup  Sorted by gauge_ft.
 */
function rate_gauge_to_flow(array $lookup, float $gauge_ft): ?float {
    $n = count($lookup);
    if ($n === 0) return null;
    if ($gauge_ft <= $lookup[0][0]) return $lookup[0][1];
    if ($gauge_ft >= $lookup[$n - 1][0]) return $lookup[$n - 1][1];
    for ($i = 0; $i < $n - 1; $i++) {
        [$g1, $f1] = $lookup[$i];
        [$g2, $f2] = $lookup[$i + 1];
        if ($g1 <= $gauge_ft && $gauge_ft <= $g2) {
            if ($g2 === $g1) return $f1;
            return $f1 + ($f2 - $f1) / ($g2 - $g1) * ($gauge_ft - $g1);
        }
    }
    return null;
}

/**
 * Inverse rating: flow cfs -> gauge ft (linear interp, clamped at endpoints).
 *
 * Assumes $lookup has monotone-increasing flow (derive_rating_lookup enforces this).
 *
 * @param array<int, array{0: float, 1: float}> $lookup  Sorted by gauge_ft, monotone in flow.
 */
function rate_flow_to_gauge(array $lookup, float $flow_cfs): ?float {
    $n = count($lookup);
    if ($n === 0) return null;
    if ($flow_cfs <= $lookup[0][1]) return $lookup[0][0];
    if ($flow_cfs >= $lookup[$n - 1][1]) return $lookup[$n - 1][0];
    for ($i = 0; $i < $n - 1; $i++) {
        [$g1, $f1] = $lookup[$i];
        [$g2, $f2] = $lookup[$i + 1];
        if ($f1 <= $flow_cfs && $flow_cfs <= $f2) {
            if ($f2 === $f1) return $g1;
            return $g1 + ($g2 - $g1) / ($f2 - $f1) * ($flow_cfs - $f1);
        }
    }
    return null;
}
