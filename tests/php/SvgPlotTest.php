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

    public function test_generate_gradient_profile_svg_drops_trailing_insig(): void
    {
        // Trailing insig after a sig bar — algorithm artifact (e.g.
        // non-monotonic DEM near the take-out). Renderer drops it so
        // the previous sig bar visually stretches to the take-out.
        $profile = json_encode([
            'samples' => [
                ['d_mi' => 0.0, 'lat' => 44.1, 'lon' => -122.0, 'grad_ft_per_mi' => 80.0, 'w_mi' => 0.5, 'significant' => true],
                ['d_mi' => 0.5, 'lat' => 44.11, 'lon' => -122.01, 'grad_ft_per_mi' => 120.0, 'w_mi' => 0.25, 'significant' => true],
                ['d_mi' => 1.0, 'lat' => 44.12, 'lon' => -122.02, 'grad_ft_per_mi' => 50.0, 'w_mi' => 1.0, 'significant' => true],
                ['d_mi' => 1.5, 'lat' => 44.13, 'lon' => -122.03, 'grad_ft_per_mi' => 10.0, 'w_mi' => 5.0, 'significant' => false],
            ],
        ]);
        assert($profile !== false);
        $svg = generate_gradient_profile_svg($profile, 1, 480, 120);
        $sigGroupHtml = preg_match('!<g class="gp-bars-sig">(.*?)</g>!', $svg, $m) ? $m[1] : '';
        $paleGroupHtml = preg_match('!<g class="gp-bars-pale">(.*?)</g>!', $svg, $m) ? $m[1] : '';
        // 3 sig kept, trailing pale dropped
        $this->assertSame(3, substr_count($sigGroupHtml, '<rect'));
        $this->assertSame(0, substr_count($paleGroupHtml, '<rect'));
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
