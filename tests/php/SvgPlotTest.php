<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/../../php/includes/svg_plot.php';

final class SvgPlotTest extends TestCase
{
    public function test_rate_gauge_to_flow_linear_interp(): void
    {
        $lookup = [[3.0, 100.0], [4.0, 200.0], [5.0, 400.0]];
        $this->assertSame(150.0, rate_gauge_to_flow($lookup, 3.5));
        $this->assertSame(100.0, rate_gauge_to_flow($lookup, 2.0));   // clamped low
        $this->assertSame(400.0, rate_gauge_to_flow($lookup, 9.0));   // clamped high
    }

    public function test_rate_flow_to_gauge_inverse(): void
    {
        $lookup = [[3.0, 100.0], [4.0, 200.0], [5.0, 400.0]];
        $this->assertSame(3.5, rate_flow_to_gauge($lookup, 150.0));
        $this->assertSame(4.5, rate_flow_to_gauge($lookup, 300.0));   // mid of [200, 400]
        $this->assertSame(3.0, rate_flow_to_gauge($lookup, 50.0));    // clamped low
        $this->assertSame(5.0, rate_flow_to_gauge($lookup, 9999.0));  // clamped high
    }

    public function test_rate_gauge_to_flow_returns_null_on_empty_lookup(): void
    {
        $this->assertNull(rate_gauge_to_flow([], 1.0));
        $this->assertNull(rate_flow_to_gauge([], 1.0));
    }

    public function test_derive_rating_lookup_from_pairs(): void
    {
        $pdo = self::make_pdo_with_observations([
            [1, '2026-04-01 00:00', 'flow',  100.0],
            [1, '2026-04-01 00:00', 'gauge',   3.0],
            [1, '2026-04-01 01:00', 'flow',  400.0],
            [1, '2026-04-01 01:00', 'gauge',   5.0],
        ]);
        $r = derive_rating_lookup($pdo, 1, 'flow', '2026-01-01');
        $this->assertNotNull($r);
        $this->assertCount(2, $r);
        $this->assertSame([3.0, 100.0], [$r[0][0], $r[0][1]]);
        $this->assertSame([5.0, 400.0], [$r[1][0], $r[1][1]]);
    }

    public function test_derive_rating_lookup_drops_non_monotone_bin(): void
    {
        // Middle pair has lower flow than the first — should be dropped to keep
        // flow monotone-increasing so the inverse flow->gauge stays well-defined.
        $pdo = self::make_pdo_with_observations([
            [1, '2026-04-01 00:00', 'flow',  100.0],
            [1, '2026-04-01 00:00', 'gauge',   3.0],
            [1, '2026-04-01 01:00', 'flow',   50.0],   // anomalous
            [1, '2026-04-01 01:00', 'gauge',   4.0],
            [1, '2026-04-01 02:00', 'flow',  400.0],
            [1, '2026-04-01 02:00', 'gauge',   5.0],
        ]);
        $r = derive_rating_lookup($pdo, 1, 'flow', '2026-01-01');
        $this->assertNotNull($r);
        $this->assertCount(2, $r);
        $this->assertSame(3.0, $r[0][0]);
        $this->assertSame(5.0, $r[1][0]);
    }

    public function test_derive_rating_lookup_drops_nonpositive_primary(): void
    {
        $pdo = self::make_pdo_with_observations([
            [1, '2026-04-01 00:00', 'flow',    0.0],   // dropped by p.value > 0
            [1, '2026-04-01 00:00', 'gauge',   3.0],
            [1, '2026-04-01 01:00', 'flow',  100.0],
            [1, '2026-04-01 01:00', 'gauge',   4.0],
        ]);
        // Only one usable pair — can't produce 2 distinct bins.
        $this->assertNull(derive_rating_lookup($pdo, 1, 'flow', '2026-01-01'));
    }

    public function test_derive_rating_lookup_rejects_unknown_type(): void
    {
        $pdo = self::make_pdo_with_observations([]);
        $this->assertNull(derive_rating_lookup($pdo, 1, 'temperature', '2026-01-01'));
    }

    public function test_generate_rating_dual_plot_emits_svg(): void
    {
        $svg = generate_rating_dual_plot(
            [time(), time() + 3600],
            [100.0, 200.0],
            [[3.0, 100.0], [4.0, 200.0]],
            'Test',
            'Flow (CFS)'
        );
        $this->assertStringContainsString('<svg', $svg);
        $this->assertStringContainsString('Flow (CFS)', $svg);
        $this->assertStringContainsString('Gage Height', $svg);
    }

    public function test_generate_rating_dual_plot_empty_on_too_few_points(): void
    {
        $svg = generate_rating_dual_plot([time()], [100.0], [[3.0, 100.0], [4.0, 200.0]], 'T', 'Flow (CFS)');
        $this->assertStringContainsString('No data available', $svg);
    }

    public function test_generate_svg_plot_emits_data_series(): void
    {
        $svg = generate_svg_plot(
            [1700000000, 1700003600, 1700007200],
            [100.0, 150.0, 200.0],
            'Test',
            'Flow (CFS)'
        );
        $payload = self::parse_data_series($svg);
        $this->assertSame('single', $payload['kind']);
        $this->assertSame('Flow', $payload['label']);
        $this->assertSame('CFS', $payload['unit']);
        $this->assertIsArray($payload['points']);
        $this->assertGreaterThanOrEqual(2, count($payload['points']));
        $this->assertSame(1700000000, $payload['points'][0][0]);
        $this->assertArrayHasKey('y_min', $payload);
        $this->assertArrayHasKey('y_max', $payload);
        $this->assertArrayHasKey('margins', $payload);
        $this->assertSame(80, $payload['margins']['ml']);
    }

    public function test_generate_rating_dual_plot_emits_data_series_with_rating(): void
    {
        $rating = [[3.0, 100.0], [4.0, 200.0], [5.0, 400.0]];
        $svg = generate_rating_dual_plot(
            [1700000000, 1700003600, 1700007200],
            [120.0, 180.0, 250.0],
            $rating,
            'Test',
            'Flow (CFS)'
        );
        $payload = self::parse_data_series($svg);
        $this->assertSame('dual', $payload['kind']);
        $this->assertSame($rating, $payload['rating']);
        $this->assertSame(1, $payload['gauge_decimals']);
        $this->assertSame('Flow', $payload['label']);
        $this->assertSame('CFS', $payload['unit']);
    }

    public function test_split_y_label_without_unit(): void
    {
        // Caller passes a label that doesn't have parens — helper returns it as-is.
        $svg = generate_svg_plot(
            [1700000000, 1700003600],
            [100.0, 200.0],
            'Test',
            'Bare Label'
        );
        $payload = self::parse_data_series($svg);
        $this->assertSame('Bare Label', $payload['label']);
        $this->assertSame('', $payload['unit']);
    }

    public function test_generate_gradient_profile_svg_empty_string_returns_empty(): void
    {
        $this->assertSame('', generate_gradient_profile_svg('', 1));
    }

    public function test_generate_gradient_profile_svg_invalid_json_returns_empty(): void
    {
        $this->assertSame('', generate_gradient_profile_svg('not-json', 1));
        $this->assertSame('', generate_gradient_profile_svg('{}', 1));
        $this->assertSame('', generate_gradient_profile_svg('{"samples": []}', 1));
        $this->assertSame('', generate_gradient_profile_svg('{"samples": [{"d_mi":0}]}', 1));
    }

    public function test_generate_gradient_profile_svg_emits_chart(): void
    {
        // Insig sample is in the MIDDLE — keeps the pale-bar assertion
        // honest without tripping the "drop trailing insig after sig"
        // logic. See test_generate_gradient_profile_svg_drops_trailing_insig
        // for that case.
        $profile = json_encode([
            'step_mi' => 0.05,
            'rmse_m' => 2.4,
            'min_drop_ft_for_significance' => 33.4,
            'samples' => [
                ['d_mi' => 0.0, 'lat' => 44.1, 'lon' => -122.0, 'grad_ft_per_mi' => 80.0, 'w_mi' => 0.5, 'significant' => true],
                ['d_mi' => 0.5, 'lat' => 44.11, 'lon' => -122.01, 'grad_ft_per_mi' => 10.0, 'w_mi' => 5.0, 'significant' => false],
                ['d_mi' => 1.0, 'lat' => 44.12, 'lon' => -122.02, 'grad_ft_per_mi' => 50.0, 'w_mi' => 1.0, 'significant' => true],
                ['d_mi' => 1.5, 'lat' => 44.13, 'lon' => -122.03, 'grad_ft_per_mi' => 120.0, 'w_mi' => 0.25, 'significant' => true],
            ],
        ]);
        assert($profile !== false);

        $svg = generate_gradient_profile_svg($profile, 407, 480, 120);
        $this->assertStringContainsString('<svg', $svg);
        $this->assertStringContainsString('gradient-profile-chart', $svg);
        $this->assertStringContainsString('data-reach-id="407"', $svg);
        $this->assertStringContainsString('data-profile=', $svg);
        // Two bar groups (sig + below-floor), each emitted regardless
        $this->assertStringContainsString('class="gp-bars-pale"', $svg);
        $this->assertStringContainsString('class="gp-bars-sig"', $svg);
        // 3 sig + 1 pale (insig is in the middle, not trailing)
        $sigGroupHtml = preg_match('!<g class="gp-bars-sig">(.*?)</g>!', $svg, $m) ? $m[1] : '';
        $paleGroupHtml = preg_match('!<g class="gp-bars-pale">(.*?)</g>!', $svg, $m) ? $m[1] : '';
        $this->assertSame(3, substr_count($sigGroupHtml, '<rect'));
        $this->assertSame(1, substr_count($paleGroupHtml, '<rect'));
    }

    public function test_generate_gradient_profile_svg_drops_short_trailing_insig(): void
    {
        // Trailing insig after a sig bar — DEM artifact territory (bridge/road
        // embankment near the take-out, typically a few hundred metres). The
        // renderer drops bars under the 0.5 mi gate so the previous sig bar
        // visually stretches to the take-out instead of trailing off.
        $profile = json_encode([
            'samples' => [
                ['d_mi' => 0.0, 'lat' => 44.1, 'lon' => -122.0, 'grad_ft_per_mi' => 80.0, 'w_mi' => 0.5, 'significant' => true],
                ['d_mi' => 0.5, 'lat' => 44.11, 'lon' => -122.01, 'grad_ft_per_mi' => 120.0, 'w_mi' => 0.25, 'significant' => true],
                ['d_mi' => 1.0, 'lat' => 44.12, 'lon' => -122.02, 'grad_ft_per_mi' => 50.0, 'w_mi' => 1.0, 'significant' => true],
                ['d_mi' => 1.5, 'lat' => 44.13, 'lon' => -122.03, 'grad_ft_per_mi' => 10.0, 'w_mi' => 0.2, 'significant' => false],
            ],
        ]);
        assert($profile !== false);
        $svg = generate_gradient_profile_svg($profile, 1, 480, 120);
        $sigGroupHtml = preg_match('!<g class="gp-bars-sig">(.*?)</g>!', $svg, $m) ? $m[1] : '';
        $paleGroupHtml = preg_match('!<g class="gp-bars-pale">(.*?)</g>!', $svg, $m) ? $m[1] : '';
        // 3 sig kept, short (<0.5 mi) trailing pale dropped
        $this->assertSame(3, substr_count($sigGroupHtml, '<rect'));
        $this->assertSame(0, substr_count($paleGroupHtml, '<rect'));
    }

    public function test_generate_gradient_profile_svg_keeps_wide_trailing_insig(): void
    {
        // A *long* (>=0.5 mi) trailing insig is real low-gradient terrain
        // (reservoir, lakeshore, navigation pool) and must render as the
        // near-zero bar it is — otherwise the previous sig bar gets visually
        // stretched across the take-out and implies a drop bigger than the
        // whole reach's elevation_lost. Mirrors the reach 419 case (Canyon
        // Creek into Merwin Reservoir: 1.6 mi insig tail).
        $profile = json_encode([
            'samples' => [
                ['d_mi' => 0.0, 'lat' => 44.1, 'lon' => -122.0, 'grad_ft_per_mi' => 80.0, 'w_mi' => 0.5, 'significant' => true],
                ['d_mi' => 0.5, 'lat' => 44.11, 'lon' => -122.01, 'grad_ft_per_mi' => 120.0, 'w_mi' => 0.25, 'significant' => true],
                ['d_mi' => 1.0, 'lat' => 44.12, 'lon' => -122.02, 'grad_ft_per_mi' => 50.0, 'w_mi' => 1.0, 'significant' => true],
                ['d_mi' => 1.5, 'lat' => 44.13, 'lon' => -122.03, 'grad_ft_per_mi' => 10.0, 'w_mi' => 1.6, 'significant' => false],
            ],
        ]);
        assert($profile !== false);
        $svg = generate_gradient_profile_svg($profile, 1, 480, 120);
        $sigGroupHtml = preg_match('!<g class="gp-bars-sig">(.*?)</g>!', $svg, $m) ? $m[1] : '';
        $paleGroupHtml = preg_match('!<g class="gp-bars-pale">(.*?)</g>!', $svg, $m) ? $m[1] : '';
        // 3 sig kept, trailing pale (1.6 mi ≥ 0.5 mi) preserved as a flat bar
        $this->assertSame(3, substr_count($sigGroupHtml, '<rect'));
        $this->assertSame(1, substr_count($paleGroupHtml, '<rect'));
    }

    public function test_generate_gradient_profile_svg_reservoir_gap_not_stretched(): void
    {
        // Reservoir at the take-out: the gradient trace stops short of
        // reach.length (no gradient data for the flat reservoir, whose gradient
        // is zero). The last real bar must NOT stretch to the take-out — the
        // gap reads as zero gradient (blank). Samples cover ~0..1.6 mi of a
        // 5 mi reach.
        $profile = json_encode([
            'samples' => [
                ['d_mi' => 0.0, 'grad_ft_per_mi' => 80.0, 'w_mi' => 0.5, 'significant' => true],
                ['d_mi' => 0.5, 'grad_ft_per_mi' => 120.0, 'w_mi' => 0.25, 'significant' => true],
                ['d_mi' => 1.0, 'grad_ft_per_mi' => 50.0, 'w_mi' => 1.0, 'significant' => true],
                ['d_mi' => 1.5, 'grad_ft_per_mi' => 40.0, 'w_mi' => 0.25, 'significant' => true],
            ],
        ]);
        assert($profile !== false);
        // length_mi = 5.0; width 480 → ml 50, pw 420, plot_right 470. The last
        // sample's right edge is ~1.625 mi (~186 px); the pre-fix stretch would
        // have pinned it to plot_right (470).
        $svg = generate_gradient_profile_svg($profile, 1, 480, 120, 5.0);
        $bars = '';
        if (preg_match('!<g class="gp-bars-sig">(.*?)</g>!', $svg, $m)) {
            $bars .= $m[1];
        }
        if (preg_match('!<g class="gp-bars-pale">(.*?)</g>!', $svg, $m)) {
            $bars .= $m[1];
        }
        preg_match_all(
            '!<rect x="([0-9.]+)" y="[0-9.]+" width="([0-9.]+)"!',
            $bars,
            $rects,
            PREG_SET_ORDER
        );
        $this->assertNotEmpty($rects);
        $maxRight = 0.0;
        foreach ($rects as $r) {
            $maxRight = max($maxRight, (float) $r[1] + (float) $r[2]);
        }
        $this->assertLessThan(400.0, $maxRight, 'last bar must not stretch to the take-out');
    }

    public function test_generate_gradient_profile_svg_leading_gap_not_stretched(): void
    {
        // A lake at the put-in: the first window starts at ~1.875 mi, not 0. The
        // first bar must NOT stretch back to the put-in (x=ml) — the leading span
        // reads as zero gradient (blank), mirroring the reservoir tail.
        $profile = json_encode([
            'samples' => [
                ['d_mi' => 2.0, 'grad_ft_per_mi' => 80.0, 'w_mi' => 0.25, 'significant' => true],
                ['d_mi' => 2.5, 'grad_ft_per_mi' => 40.0, 'w_mi' => 0.25, 'significant' => true],
            ],
        ]);
        assert($profile !== false);
        // length 5 mi, width 480 → ml 50, pw 420, scale 84; first window left
        // 1.875 mi → x ≈ 207.5. The old first-bar stretch would have pinned it
        // to ml (50).
        $svg = generate_gradient_profile_svg($profile, 1, 480, 120, length_mi: 5.0);
        $bars = '';
        if (preg_match('!<g class="gp-bars-sig">(.*?)</g>!', $svg, $m)) {
            $bars .= $m[1];
        }
        if (preg_match('!<g class="gp-bars-pale">(.*?)</g>!', $svg, $m)) {
            $bars .= $m[1];
        }
        preg_match_all('!<rect x="([0-9.]+)"!', $bars, $rects, PREG_SET_ORDER);
        $this->assertNotEmpty($rects);
        $minLeft = min(array_map(static fn (array $r): float => (float) $r[1], $rects));
        $this->assertGreaterThan(100.0, $minLeft, 'first bar must not stretch to the put-in');
    }

    public function test_generate_gradient_profile_svg_all_overshoot_draws_no_bars(): void
    {
        // Every bin is past the take-out (d_mi 100/101 on a 5 mi reach). With no
        // first-bar stretch, all bars clamp to zero width and are skipped — the
        // chart agrees with the elevation/no-data layer instead of drawing a
        // full-width bar for an out-of-domain sample.
        $profile = json_encode([
            'samples' => [
                ['d_mi' => 100.0, 'grad_ft_per_mi' => 80.0, 'w_mi' => 1.0, 'significant' => true],
                ['d_mi' => 101.0, 'grad_ft_per_mi' => 40.0, 'w_mi' => 1.0, 'significant' => true],
            ],
        ]);
        assert($profile !== false);
        $svg = generate_gradient_profile_svg($profile, 1, 480, 120, length_mi: 5.0);
        $bars = '';
        if (preg_match('!<g class="gp-bars-sig">(.*?)</g>!', $svg, $m)) {
            $bars .= $m[1];
        }
        if (preg_match('!<g class="gp-bars-pale">(.*?)</g>!', $svg, $m)) {
            $bars .= $m[1];
        }
        $this->assertSame(0, substr_count($bars, '<rect'), 'no bar for out-of-domain samples');
    }

    public function test_generate_gradient_profile_svg_elevation_flat_over_leading_gap(): void
    {
        // A lake at the put-in (first window ~1.875 mi) with elevation params:
        // the elevation line must stay flat at the put-in elevation across the
        // blank leading span, not slope a gradient-derived drop into it.
        $profile = json_encode([
            'samples' => [
                ['d_mi' => 2.0, 'grad_ft_per_mi' => 80.0, 'w_mi' => 0.25, 'significant' => true],
                ['d_mi' => 2.5, 'grad_ft_per_mi' => 40.0, 'w_mi' => 0.25, 'significant' => true],
            ],
        ]);
        assert($profile !== false);
        $svg = generate_gradient_profile_svg(
            $profile,
            1,
            480,
            120,
            length_mi: 5.0,
            putin_elev_ft: 1000.0,
            elev_lost_ft: 500.0
        );
        $this->assertSame(1, preg_match('!<polyline class="gp-elev" points="([^"]+)"!', $svg, $m));
        $pts = array_map(
            static fn (string $p): array => array_map('floatval', explode(',', $p)),
            explode(' ', trim($m[1]))
        );
        $putinY = $pts[0][1]; // first point is the put-in
        // A flat leading segment = a point at the put-in elevation to the right
        // of the put-in (x>ml); the drop only begins at the first window.
        $flatBeyond = array_filter(
            $pts,
            static fn (array $pt): bool => $pt[0] > 50.5 && abs($pt[1] - $putinY) < 0.5
        );
        $this->assertNotEmpty($flatBeyond, 'elevation must stay flat across the leading gap');
    }

    public function test_generate_gradient_profile_svg_splits_runs_on_insignificance(): void
    {
        // 5 samples: 4 sig + 1 insig → 4 sig rects + 1 pale rect, regardless
        // of whether the sig runs are contiguous (every sample = one bar now).
        $profile = json_encode([
            'samples' => [
                ['d_mi' => 0.0, 'lat' => 0, 'lon' => 0, 'grad_ft_per_mi' => 80.0, 'w_mi' => 0.5, 'significant' => true],
                ['d_mi' => 0.5, 'lat' => 0, 'lon' => 0, 'grad_ft_per_mi' => 90.0, 'w_mi' => 0.5, 'significant' => true],
                ['d_mi' => 1.0, 'lat' => 0, 'lon' => 0, 'grad_ft_per_mi' => 10.0, 'w_mi' => 5.0, 'significant' => false],
                ['d_mi' => 1.5, 'lat' => 0, 'lon' => 0, 'grad_ft_per_mi' => 75.0, 'w_mi' => 0.5, 'significant' => true],
                ['d_mi' => 2.0, 'lat' => 0, 'lon' => 0, 'grad_ft_per_mi' => 70.0, 'w_mi' => 0.5, 'significant' => true],
            ],
        ]);
        assert($profile !== false);
        $svg = generate_gradient_profile_svg($profile, 1, 480, 120);
        $sigGroupHtml = preg_match('!<g class="gp-bars-sig">(.*?)</g>!', $svg, $m) ? $m[1] : '';
        $paleGroupHtml = preg_match('!<g class="gp-bars-pale">(.*?)</g>!', $svg, $m) ? $m[1] : '';
        $this->assertSame(4, substr_count($sigGroupHtml, '<rect'));
        $this->assertSame(1, substr_count($paleGroupHtml, '<rect'));
    }

    public function test_generate_gradient_profile_svg_emits_elevation_line(): void
    {
        $profile = json_encode([
            'samples' => [
                ['d_mi' => 0.0, 'grad_ft_per_mi' => 80.0, 'w_mi' => 0.5, 'significant' => true],
                ['d_mi' => 0.5, 'grad_ft_per_mi' => 50.0, 'w_mi' => 1.0, 'significant' => true],
                ['d_mi' => 1.0, 'grad_ft_per_mi' => 120.0, 'w_mi' => 0.25, 'significant' => true],
            ],
        ]);
        assert($profile !== false);

        // With elevation params $has_elev is true: the polyline + right-axis
        // labels render, themed via .gp-elev / .gp-elev-axis (no inline color),
        // and the JS payload carries put-in/take-out elevation anchors.
        $svg = generate_gradient_profile_svg(
            $profile,
            407,
            480,
            120,
            putin_elev_ft: 2400.0,
            elev_lost_ft: 600.0
        );
        $this->assertStringContainsString('<polyline class="gp-elev"', $svg);
        $this->assertStringContainsString('class="gp-elev-axis"', $svg);
        $this->assertStringNotContainsString('#1565C0', $svg);  // moved to CSS

        $payload = self::parse_gradient_payload($svg);
        $this->assertIsArray($payload['elev']);
        $this->assertEqualsWithDelta(2400.0, $payload['elev']['putin'], 0.01);
        $this->assertEqualsWithDelta(1800.0, $payload['elev']['takeout'], 0.01);
    }

    public function test_generate_gradient_profile_svg_elevation_clips_overshoot(): void
    {
        // A bin centred past the take-out (overshoot: d_mi=10 on a 5 mi reach)
        // must not plot the elevation line outside the chart — consistent with
        // the clipped bars + hover no-data contract.
        $profile = json_encode([
            'samples' => [
                ['d_mi' => 0.0, 'grad_ft_per_mi' => 80.0, 'w_mi' => 0.5, 'significant' => true],
                ['d_mi' => 1.0, 'grad_ft_per_mi' => 50.0, 'w_mi' => 1.0, 'significant' => true],
                ['d_mi' => 10.0, 'grad_ft_per_mi' => 40.0, 'w_mi' => 0.25, 'significant' => true],
            ],
        ]);
        assert($profile !== false);
        $svg = generate_gradient_profile_svg(
            $profile,
            1,
            480,
            120,
            length_mi: 5.0,
            putin_elev_ft: 2400.0,
            elev_lost_ft: 600.0
        );
        // With elevation, mr=48 → pw=382, plot_right = ml(50) + 382 = 432.
        $this->assertSame(1, preg_match('!<polyline class="gp-elev" points="([^"]+)"!', $svg, $m));
        $xs = array_map(
            static fn (string $pt): float => (float) explode(',', $pt)[0],
            explode(' ', trim($m[1]))
        );
        $this->assertNotEmpty($xs);
        foreach ($xs as $x) {
            $this->assertLessThanOrEqual(432.5, $x, 'elevation point must stay within the chart');
        }
    }

    public function test_generate_gradient_profile_svg_omits_elevation_without_params(): void
    {
        // 4-arg form: no elevation params → no line, no axis, elev payload null.
        $profile = json_encode([
            'samples' => [
                ['d_mi' => 0.0, 'grad_ft_per_mi' => 80.0, 'w_mi' => 0.5, 'significant' => true],
                ['d_mi' => 0.5, 'grad_ft_per_mi' => 50.0, 'w_mi' => 1.0, 'significant' => true],
            ],
        ]);
        assert($profile !== false);
        $svg = generate_gradient_profile_svg($profile, 1, 480, 120);
        $this->assertStringNotContainsString('gp-elev', $svg);
        $this->assertNull(self::parse_gradient_payload($svg)['elev']);
    }

    // -----------------------------------------------------------------
    // _empty_svg via too-few data points
    // -----------------------------------------------------------------

    public function test_generate_svg_plot_empty_times_returns_no_data(): void
    {
        $svg = generate_svg_plot([], [], 'Empty', 'Flow (CFS)');
        $this->assertStringContainsString('No data available', $svg);
        $this->assertStringContainsString('Empty', $svg);
    }

    public function test_generate_svg_plot_single_point_returns_no_data(): void
    {
        // Fewer than 2 non-null pairs → empty plot.
        $svg = generate_svg_plot([1700000000], [100.0], 'One', 'Flow (CFS)');
        $this->assertStringContainsString('No data available', $svg);
    }

    public function test_generate_svg_plot_drops_null_values(): void
    {
        // Nulls are gaps — with only one real value left, the plot is empty.
        $svg = generate_svg_plot(
            [1700000000, 1700003600, 1700007200],
            [null, 150.0, null],
            'Gappy',
            'Flow (CFS)'
        );
        $this->assertStringContainsString('No data available', $svg);
    }

    public function test_generate_rating_dual_plot_empty_times_returns_no_data(): void
    {
        $svg = generate_rating_dual_plot([], [], [[3.0, 100.0], [4.0, 200.0]], 'T', 'Flow (CFS)');
        $this->assertStringContainsString('No data available', $svg);
    }

    // -----------------------------------------------------------------
    // _bands_svg — exercised through generate_svg_plot($bands)
    // -----------------------------------------------------------------

    public function test_bands_low_and_high_emit_three_zones(): void
    {
        // low + high present → low/okay/high zones; expect <= 3 band rects
        // (a zone clipped to zero height is skipped).
        $svg = generate_svg_plot(
            [1700000000, 1700003600, 1700007200],
            [50.0, 250.0, 450.0],
            'Bands',
            'Flow (CFS)',
            800,
            350,
            200,
            true,
            ['low' => 150.0, 'high' => 350.0]
        );
        $n = substr_count($svg, 'fill-opacity="0.12"');
        $this->assertGreaterThanOrEqual(2, $n);
        $this->assertLessThanOrEqual(3, $n);
        // Band colours come from the low/okay/high palette.
        $this->assertStringContainsString('#4caf50', $svg); // okay green present
    }

    public function test_bands_low_only(): void
    {
        // Only a low threshold → low + okay zones (no high red).
        $svg = generate_svg_plot(
            [1700000000, 1700003600],
            [50.0, 450.0],
            'LowOnly',
            'Flow (CFS)',
            800,
            350,
            200,
            true,
            ['low' => 200.0]
        );
        $this->assertStringContainsString('fill-opacity="0.12"', $svg);
        $this->assertStringNotContainsString('#e53935', $svg); // no high-zone red
    }

    public function test_bands_high_only(): void
    {
        // Only a high threshold → okay + high zones (no low amber).
        $svg = generate_svg_plot(
            [1700000000, 1700003600],
            [50.0, 450.0],
            'HighOnly',
            'Flow (CFS)',
            800,
            350,
            200,
            true,
            ['high' => 200.0]
        );
        $this->assertStringContainsString('#e53935', $svg);   // high red present
        $this->assertStringNotContainsString('#e8a735', $svg); // no low amber
    }

    public function test_bands_null_emits_no_band_rects(): void
    {
        // bands array present but both keys null → no band rects at all.
        $svg = generate_svg_plot(
            [1700000000, 1700003600],
            [50.0, 450.0],
            'NoBand',
            'Flow (CFS)',
            800,
            350,
            200,
            true,
            ['low' => null, 'high' => null]
        );
        $this->assertStringNotContainsString('fill-opacity="0.12"', $svg);
    }

    // -----------------------------------------------------------------
    // nice_axis — direct, including the fallback tick search
    // -----------------------------------------------------------------

    public function test_nice_axis_round_bounds_and_step(): void
    {
        [$lo, $hi, $step] = nice_axis(0.0, 100.0);
        $this->assertLessThanOrEqual(0.0, $lo);
        $this->assertGreaterThanOrEqual(100.0, $hi);
        $this->assertGreaterThan(0.0, $step);
        // Bounds are integral multiples of the step.
        $this->assertEqualsWithDelta(0.0, fmod($lo, $step), 1e-9);
        $this->assertEqualsWithDelta(0.0, fmod($hi, $step), 1e-9);
    }

    public function test_nice_axis_zero_range_does_not_divide_by_zero(): void
    {
        // data_min == data_max → range forced to 1.0; returns a sane window.
        [$lo, $hi, $step] = nice_axis(5.0, 5.0);
        $this->assertGreaterThan(0.0, $step);
        $this->assertLessThanOrEqual(5.0, $lo);
        $this->assertGreaterThanOrEqual(5.0, $hi);
    }

    public function test_nice_axis_tiny_fractional_range(): void
    {
        // Sub-unit range exercises the smaller candidate steps (0.1/0.2 * mag).
        [$lo, $hi, $step] = nice_axis(3.02, 3.07);
        $this->assertLessThanOrEqual(3.02, $lo);
        $this->assertGreaterThanOrEqual(3.07, $hi);
        $this->assertGreaterThan(0.0, $step);
    }

    // -----------------------------------------------------------------
    // generate_rating_dual_plot — right-axis tick fallback path
    // -----------------------------------------------------------------

    public function test_rating_dual_plot_right_axis_fallback_to_endpoints(): void
    {
        // A steep, narrow rating curve with flow values whose "nice" gauge
        // ticks may not land inside the visible flow window forces the
        // endpoint-fallback branch for the right axis. Either way the plot
        // must render with gage-height labelling and at least one right tick.
        $rating = [[2.50, 1000.0], [2.55, 5000.0], [2.60, 9000.0]];
        $svg = generate_rating_dual_plot(
            [1700000000, 1700003600, 1700007200],
            [3000.0, 5000.0, 7000.0],
            $rating,
            'Steep',
            'Flow (CFS)'
        );
        $this->assertStringContainsString('Gage Height', $svg);
        // Right-axis ticks use the #C04020 colour.
        $this->assertStringContainsString('#C04020', $svg);
    }

    // -----------------------------------------------------------------
    // generate_gradient_profile_svg — x_max falls back to sample extent
    // -----------------------------------------------------------------

    public function test_gradient_profile_xmax_from_samples_without_length(): void
    {
        // No $length_mi → x_max derived from the last sample's d_mi.
        $profile = json_encode([
            'samples' => [
                ['d_mi' => 0.0, 'grad_ft_per_mi' => 40.0, 'w_mi' => 0.5, 'significant' => true],
                ['d_mi' => 1.0, 'grad_ft_per_mi' => 60.0, 'w_mi' => 0.5, 'significant' => true],
                ['d_mi' => 2.0, 'grad_ft_per_mi' => 50.0, 'w_mi' => 0.5, 'significant' => true],
            ],
        ]);
        assert($profile !== false);
        $svg = generate_gradient_profile_svg($profile, 9, 480, 120);
        $payload = self::parse_gradient_payload($svg);
        $this->assertSame(0.0, $payload['x_min']);
        // Last d_mi is 2.0 → that becomes x_max.
        $this->assertEqualsWithDelta(2.0, $payload['x_max'], 1e-9);
    }

    public function test_gradient_profile_carries_putin_takeout_coords(): void
    {
        // Supplying length + put-in/take-out coords populates the JS payload
        // anchors the map dot interpolates against.
        $profile = json_encode([
            'samples' => [
                ['d_mi' => 0.0, 'lat' => 44.1, 'lon' => -122.0, 'grad_ft_per_mi' => 40.0, 'w_mi' => 0.5, 'significant' => true],
                ['d_mi' => 1.0, 'lat' => 44.2, 'lon' => -122.1, 'grad_ft_per_mi' => 60.0, 'w_mi' => 0.5, 'significant' => true],
            ],
        ]);
        assert($profile !== false);
        $svg = generate_gradient_profile_svg(
            $profile,
            9,
            480,
            120,
            length_mi: 1.5,
            putin_lat: 44.10,
            putin_lon: -122.00,
            takeout_lat: 44.25,
            takeout_lon: -122.15
        );
        $payload = self::parse_gradient_payload($svg);
        $this->assertEqualsWithDelta(1.5, $payload['x_max'], 1e-9);
        $this->assertSame(['lat' => 44.10, 'lon' => -122.00], $payload['putin']);
        $this->assertSame(['lat' => 44.25, 'lon' => -122.15], $payload['takeout']);
    }

    /** Extract the JSON payload from the <svg data-series="..."> attribute. */
    private static function parse_data_series(string $svg): array
    {
        if (!preg_match('/data-series="([^"]+)"/', $svg, $m)) {
            throw new \RuntimeException('no data-series attribute found');
        }
        $json = html_entity_decode($m[1], ENT_QUOTES, 'UTF-8');
        $payload = json_decode($json, true);
        if (!is_array($payload)) {
            throw new \RuntimeException('data-series JSON decode failed');
        }
        return $payload;
    }

    /** Extract the JSON payload from the <svg data-profile="..."> attribute. */
    private static function parse_gradient_payload(string $svg): array
    {
        if (!preg_match('/data-profile="([^"]+)"/', $svg, $m)) {
            throw new \RuntimeException('no data-profile attribute found');
        }
        $json = html_entity_decode($m[1], ENT_QUOTES, 'UTF-8');
        $payload = json_decode($json, true);
        if (!is_array($payload)) {
            throw new \RuntimeException('data-profile JSON decode failed');
        }
        return $payload;
    }

    /**
     * @param list<array{int, string, string, float}> $observations  (source_id, observed_at, data_type, value)
     */
    private static function make_pdo_with_observations(array $observations): PDO
    {
        $pdo = new PDO('sqlite::memory:');
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $pdo->exec("CREATE TABLE gauge_source (gauge_id INT, source_id INT);
                    CREATE TABLE observation (source_id INT, observed_at TEXT,
                                              data_type TEXT, value REAL);
                    INSERT INTO gauge_source VALUES (1, 1);");
        $ins = $pdo->prepare('INSERT INTO observation VALUES (?, ?, ?, ?)');
        foreach ($observations as $row) {
            $ins->execute($row);
        }
        return $pdo;
    }
}
