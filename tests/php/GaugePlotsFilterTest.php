<?php

declare(strict_types=1);

use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/../../src/kayak/web/php/includes/gauge_plots_filter.php';

/**
 * Pure unit tests for gauge_plots_filter.php — no DB, no output. The three
 * helpers (`_gp_lower_bound`, `_gp_despike_per_source`, `_gp_cross_source_mean`)
 * operate on parallel (times, values, [sources]) arrays. The aggregation pipeline
 * is per-source-mean-then-cross-source-mean (mean-of-means), so a low-cadence
 * source counts equally to a high-cadence one at the cross step.
 */
final class GaugePlotsFilterTest extends TestCase
{
    // ---- _gp_lower_bound -------------------------------------------------

    public function test_lower_bound_empty_array(): void
    {
        $this->assertSame(0, _gp_lower_bound([], 5));
    }

    public function test_lower_bound_target_before_all(): void
    {
        $this->assertSame(0, _gp_lower_bound([10, 20, 30], 5));
    }

    public function test_lower_bound_target_after_all(): void
    {
        $this->assertSame(3, _gp_lower_bound([10, 20, 30], 99));
    }

    public function test_lower_bound_exact_match_returns_that_index(): void
    {
        // Lower-bound semantics: first index whose element is >= target.
        $this->assertSame(1, _gp_lower_bound([10, 20, 30], 20));
    }

    public function test_lower_bound_between_elements(): void
    {
        $this->assertSame(2, _gp_lower_bound([10, 20, 30, 40], 25));
    }

    public function test_lower_bound_duplicate_values_returns_first(): void
    {
        $this->assertSame(1, _gp_lower_bound([10, 20, 20, 20, 30], 20));
    }

    // ---- _gp_despike_per_source -----------------------------------------

    public function test_despike_empty_input(): void
    {
        [$t, $v, $s] = _gp_despike_per_source([], [], []);
        $this->assertSame([], $t);
        $this->assertSame([], $v);
        $this->assertSame([], $s);
    }

    public function test_despike_short_window_keeps_everything(): void
    {
        // A 2-point source window (< 3) has too little info; both points stay
        // even though they differ wildly.
        $t = [1000, 1300];
        $v = [10.0, 9999.0];
        $s = [1, 1];
        [$ot, $ov, $os] = _gp_despike_per_source($t, $v, $s);
        $this->assertCount(2, $ot);
        $this->assertSame([10.0, 9999.0], $ov);
        $this->assertSame([1, 1], $os);
    }

    public function test_despike_drops_obvious_spike_against_clean_signal(): void
    {
        // Five samples 60s apart from one source, all ~10.0 except a 500.0 spike.
        // With MAD≈0 the relative+floor threshold (0.25*|median|+0.5) catches it.
        $t = [0, 60, 120, 180, 240];
        $v = [10.0, 10.0, 500.0, 10.0, 10.0];
        $s = [1, 1, 1, 1, 1];
        [$ot, $ov, $os] = _gp_despike_per_source($t, $v, $s);
        $this->assertNotContains(500.0, $ov);
        $this->assertCount(4, $ot);
        // Source labels preserved on the survivors.
        $this->assertSame([1, 1, 1, 1], $os);
    }

    public function test_despike_keeps_legitimate_variability(): void
    {
        // Gently rising series within a window — MAD-scaled threshold keeps all.
        $t = [0, 60, 120, 180, 240];
        $v = [10.0, 11.0, 12.0, 13.0, 14.0];
        $s = [1, 1, 1, 1, 1];
        [$ot, $ov] = _gp_despike_per_source($t, $v, $s);
        $this->assertCount(5, $ot);
        $this->assertSame($v, $ov);
    }

    public function test_despike_per_source_independent(): void
    {
        // Two interleaved sources; the spike belongs to source 2 only and must
        // be judged against source 2's own window, not the merged stream.
        $t = [0, 0, 60, 60, 120, 120, 180, 180, 240, 240];
        $v = [
            10.0, 20.0,
            10.0, 20.0,
            10.0, 900.0,   // source-2 spike at t=120
            10.0, 20.0,
            10.0, 20.0,
        ];
        $s = [1, 2, 1, 2, 1, 2, 1, 2, 1, 2];
        [$ot, $ov, $os] = _gp_despike_per_source($t, $v, $s);
        // Source-1 points all survive (steady at 10.0).
        $src1 = array_values(array_filter(
            $ov,
            fn($val, $i) => $os[$i] === 1,
            ARRAY_FILTER_USE_BOTH
        ));
        $this->assertSame([10.0, 10.0, 10.0, 10.0, 10.0], $src1);
        // The source-2 spike is gone.
        $this->assertNotContains(900.0, $ov);
        $this->assertCount(9, $ot);
    }

    public function test_despike_even_window_uses_median_average(): void
    {
        // Six in-window points → even count → median is the average of the two
        // central order statistics. The far-out spike is dropped.
        $t = [0, 30, 60, 90, 120, 150];
        $v = [10.0, 10.0, 11.0, 11.0, 12.0, 800.0];
        $s = [1, 1, 1, 1, 1, 1];
        [$ot, $ov] = _gp_despike_per_source($t, $v, $s);
        $this->assertNotContains(800.0, $ov);
        $this->assertLessThan(6, count($ot));
    }

    // ---- _gp_cross_source_mean ------------------------------------------

    public function test_cross_source_mean_too_few_points_returned_verbatim(): void
    {
        [$t, $v] = _gp_cross_source_mean([1000], [42.0], [1]);
        $this->assertSame([1000], $t);
        $this->assertSame([42.0], $v);
    }

    public function test_cross_source_mean_single_source_is_smoothing_mean(): void
    {
        // One source, three points all inside one window → each output point is
        // the windowed mean of that single source.
        $t = [0, 60, 120];
        $v = [10.0, 20.0, 30.0];
        $s = [1, 1, 1];
        [$ot, $ov] = _gp_cross_source_mean($t, $v, $s);
        $this->assertSame([0, 60, 120], $ot);
        // At t=0: window [0,60] forward (latest-t guard truncates future at the
        // boundary only) — t=0 is > 900s from latest? No: latest=120, 120-0<900
        // so forward half collapses; window = [t-900, 0] = just t=0 → 10.0.
        $this->assertSame(10.0, $ov[0]);
    }

    public function test_cross_source_mean_two_sources_mean_of_means(): void
    {
        // Two sources at the SAME timestamp: source 1 = 100, source 2 = 200.
        // Per-source mean: 100 and 200; cross-source mean = 150. The latest-t
        // edge guard makes every window one-sided here (single timestamp), so
        // the result is exactly the mean-of-means at that instant.
        $t = [1000, 1000];
        $v = [100.0, 200.0];
        $s = [1, 2];
        [$ot, $ov] = _gp_cross_source_mean($t, $v, $s);
        $this->assertSame([1000], $ot);          // duplicate timestamp collapsed
        $this->assertSame([150.0], $ov);          // (100 + 200) / 2
    }

    public function test_cross_source_mean_unequal_cadence_equal_weight(): void
    {
        // Source 1 fires 3x in the window, source 2 once. Mean-of-means must
        // give each source equal weight: source-1 mean (10) and source-2 mean
        // (40) average to 25 — NOT the sample-weighted (10+10+10+40)/4 = 17.5.
        // All five timestamps inside one ±900s window around t=400.
        $t = [400, 400, 460, 520, 580];
        $v = [
            10.0,   // src1 @400
            40.0,   // src2 @400
            10.0,   // src1 @460
            10.0,   // src1 @520
            10.0,   // src1 @580   (kept to extend window but src1 stays 10)
        ];
        $s = [1, 2, 1, 1, 1];
        [$ot, $ov] = _gp_cross_source_mean($t, $v, $s);
        // First output timestamp = 400. latest_t = 580; 580-400 = 180 < 900, so
        // the forward half collapses (window_end = t). Window [t-900, 400] picks
        // up only the two @400 samples: src1 mean 10, src2 mean 40 → 25.
        $this->assertSame(400, $ot[0]);
        $this->assertSame(25.0, $ov[0]);
    }

    public function test_cross_source_mean_window_truncates_near_latest(): void
    {
        // Wide spacing so the earliest point's forward half spans real samples,
        // while the latest point's forward half is empty. Two sources interleaved.
        $t = [0, 0, 1800, 1800, 3600, 3600];
        $v = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0];
        $s = [1, 2, 1, 2, 1, 2];
        [$ot, $ov] = _gp_cross_source_mean($t, $v, $s);
        $this->assertSame([0, 1800, 3600], $ot);
        // t=0: latest=3600, 3600-0 > 900 so forward half stays (±900). Window
        // [-900, 900] picks up only the two @0 samples → mean-of-means (10,20)=15.
        $this->assertSame(15.0, $ov[0]);
        // t=3600 (latest): forward half collapses; window [2700, 3600] →
        // the two @3600 samples → (50,60) = 55.
        $this->assertSame(55.0, $ov[2]);
    }

    public function test_cross_source_mean_out_of_order_input_is_sorted(): void
    {
        // Unsorted input: helper sorts internally by time before windowing.
        $t = [120, 0, 60];
        $v = [30.0, 10.0, 20.0];
        $s = [1, 1, 1];
        [$ot] = _gp_cross_source_mean($t, $v, $s);
        $this->assertSame([0, 60, 120], $ot);
    }
}
