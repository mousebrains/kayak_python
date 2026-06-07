<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/../../src/kayak/web/php/includes/lttb.php';

/**
 * Unit tests for the LTTB downsampler in php/includes/lttb.php.
 * Pure function — no DB, no HTTP.
 */
final class LttbTest extends TestCase
{
    /** Build a sine-ish series of [x, y] pairs sorted by x. */
    private static function series(int $n): array
    {
        $out = [];
        for ($i = 0; $i < $n; $i++) {
            $out[] = [$i, sin($i / 3.0) * 100.0];
        }
        return $out;
    }

    // --- passthrough guards ----------------------------------------------

    public function test_passthrough_when_threshold_ge_count(): void
    {
        $data = self::series(5);
        // threshold == n → return input verbatim
        $this->assertSame($data, lttb_downsample($data, 5));
        // threshold > n → return input verbatim
        $this->assertSame($data, lttb_downsample($data, 10));
    }

    public function test_passthrough_when_threshold_below_three(): void
    {
        $data = self::series(10);
        // A meaningful triangle needs >= 3 buckets; below that, no-op.
        $this->assertSame($data, lttb_downsample($data, 2));
        $this->assertSame($data, lttb_downsample($data, 0));
    }

    public function test_passthrough_on_empty(): void
    {
        // n=0, threshold 200 → threshold >= n → empty verbatim.
        $this->assertSame([], lttb_downsample([], 200));
    }

    // --- core downsampling behaviour -------------------------------------

    public function test_count_is_exactly_threshold(): void
    {
        $data = self::series(100);
        $out = lttb_downsample($data, 10);
        $this->assertCount(10, $out);
    }

    public function test_endpoints_preserved(): void
    {
        $data = self::series(100);
        $out = lttb_downsample($data, 12);
        // First and last input points must survive unchanged.
        $this->assertSame($data[0], $out[0]);
        $this->assertSame($data[99], $out[count($out) - 1]);
    }

    public function test_output_x_is_monotonic_nondecreasing(): void
    {
        $data = self::series(250);
        $out = lttb_downsample($data, 30);
        $prev = $out[0][0];
        foreach (array_slice($out, 1) as $p) {
            $this->assertGreaterThanOrEqual($prev, $p[0]);
            $prev = $p[0];
        }
    }

    public function test_picked_points_are_real_input_points(): void
    {
        $data = self::series(60);
        $out = lttb_downsample($data, 15);
        // LTTB selects actual samples — never interpolates.
        foreach ($out as $p) {
            $this->assertContains($p, $data);
        }
    }

    public function test_min_threshold_three_keeps_endpoints_and_one_middle(): void
    {
        // threshold == 3 is the smallest non-passthrough case: one bucket
        // between the two endpoints, exercising the single-iteration loop.
        $data = self::series(40);
        $out = lttb_downsample($data, 3);
        $this->assertCount(3, $out);
        $this->assertSame($data[0], $out[0]);
        $this->assertSame($data[39], $out[2]);
    }

    public function test_last_bucket_clamped_to_n_minus_one(): void
    {
        // A small n with threshold n-1 forces avg_end past n-1 so the
        // min(..., n-1) clamp on $avg_end / $range_end is taken.
        $data = self::series(6);   // n=6
        $out = lttb_downsample($data, 5);
        $this->assertCount(5, $out);
        $this->assertSame($data[0], $out[0]);
        $this->assertSame($data[5], $out[4]);
    }
}
