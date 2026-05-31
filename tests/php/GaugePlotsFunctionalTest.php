<?php

declare(strict_types=1);

require_once __DIR__ . '/FunctionalTestCase.php';
require_once __DIR__ . '/Fixtures.php';
require_once __DIR__ . '/../../php/includes/db.php';
require_once __DIR__ . '/../../php/includes/validate.php';
require_once __DIR__ . '/../../php/includes/gauge_plots.php';

/**
 * In-process functional tests for the gauge-plots orchestration cluster:
 *   - gauge_plots.php       (gp_resolve_window / gp_render_date_form /
 *                            gp_render_plots, _gp_bands_for_axis, dual-axis wiring)
 *   - gauge_plots_data.php  (_gp_has_obs / _gp_has_current_obs / _gp_fetch_series)
 *
 * Each gauge is seeded once per class with a distinct observation shape so the
 * decision tree in gp_render_plots() is exercised end-to-end (dual rating plot,
 * single-axis fallback, gauge-only, temperature, multi-source aggregation,
 * empty). pcov sees the executed lines because we call the functions directly.
 */
final class GaugePlotsFunctionalTest extends FunctionalTestCase
{
    /** Gauge with paired flow+gauge obs (current) → dual rating plot. */
    private static int $dualGauge = 0;
    /** Gauge with flow only → single-axis flow plot. */
    private static int $flowOnlyGauge = 0;
    /** Gauge with gauge-height only → single-axis gauge plot. */
    private static int $gaugeOnlyGauge = 0;
    /** Gauge with inflow + temperature (no flow) → inflow primary + temp. */
    private static int $inflowGauge = 0;
    /** Gauge with two sources contributing flow → cross-source aggregation. */
    private static int $multiSrcGauge = 0;
    /** Gauge with no observations at all. */
    private static int $emptyGauge = 0;
    /** Gauge with flow+gauge but data older than 6h → not "current". */
    private static int $staleGauge = 0;
    /** Gauge with current flow + gauge but UNPAIRED timestamps → no rating
     *  lookup → single-axis flow + gauge fallback (not dual). */
    private static int $unpairedGauge = 0;
    /** Gauge with exactly one gauge-height point → single plot skipped (<2). */
    private static int $onePointGauge = 0;

    protected static function seedDatabase(PDO $db): void
    {
        // ---- dual rating-curve gauge: paired flow+gauge, spread over gauge ft
        //      so derive_rating_lookup yields >= 2 monotone bins, and "current"
        //      (within 6h) so the default-view dual path fires.
        self::$dualGauge = Fixtures::gauge($db, ['river' => 'Dual River', 'state' => 'OR']);
        $dualSrc = Fixtures::source($db);
        Fixtures::linkGaugeSource($db, self::$dualGauge, $dualSrc);
        // Paired points: gauge 3.0->5.0, flow 100->500 (monotone increasing).
        $rows = [
            [3.0, 100.0],
            [3.5, 180.0],
            [4.0, 260.0],
            [4.5, 360.0],
            [5.0, 500.0],
        ];
        foreach ($rows as $i => [$g, $f]) {
            // Stagger within the last few hours (all "current" + in the 10-day window).
            $ts = gmdate('Y-m-d H:i:s', time() - $i * 1800);
            Fixtures::observation($db, $dualSrc, ['data_type' => 'flow', 'observed_at' => $ts, 'value' => $f]);
            Fixtures::observation($db, $dualSrc, ['data_type' => 'gauge', 'observed_at' => $ts, 'value' => $g]);
        }

        // ---- flow-only gauge (current flow, no gauge height)
        self::$flowOnlyGauge = Fixtures::gauge($db, ['river' => 'Flow River', 'state' => 'OR']);
        $flowSrc = Fixtures::source($db);
        Fixtures::linkGaugeSource($db, self::$flowOnlyGauge, $flowSrc);
        for ($i = 0; $i < 4; $i++) {
            $ts = gmdate('Y-m-d H:i:s', time() - $i * 3600);
            Fixtures::observation($db, $flowSrc, ['data_type' => 'flow', 'observed_at' => $ts, 'value' => 200.0 + $i]);
        }

        // ---- gauge-height-only gauge (no flow/inflow at all)
        self::$gaugeOnlyGauge = Fixtures::gauge($db, ['river' => 'Stage River', 'state' => 'WA']);
        $gSrc = Fixtures::source($db);
        Fixtures::linkGaugeSource($db, self::$gaugeOnlyGauge, $gSrc);
        for ($i = 0; $i < 4; $i++) {
            $ts = gmdate('Y-m-d H:i:s', time() - $i * 3600);
            Fixtures::observation($db, $gSrc, ['data_type' => 'gauge', 'observed_at' => $ts, 'value' => 4.0 + $i * 0.1]);
        }

        // ---- inflow + temperature gauge (no flow → inflow is primary)
        self::$inflowGauge = Fixtures::gauge($db, ['river' => 'Reservoir', 'state' => 'ID']);
        $inSrc = Fixtures::source($db);
        Fixtures::linkGaugeSource($db, self::$inflowGauge, $inSrc);
        for ($i = 0; $i < 4; $i++) {
            $ts = gmdate('Y-m-d H:i:s', time() - $i * 3600);
            Fixtures::observation($db, $inSrc, ['data_type' => 'inflow', 'observed_at' => $ts, 'value' => 80.0 + $i]);
            Fixtures::observation($db, $inSrc, ['data_type' => 'temperature', 'observed_at' => $ts, 'value' => 55.0 + $i]);
        }

        // ---- multi-source gauge: two sources both reporting flow at offset
        //      timestamps so _gp_fetch_series runs the despike + cross-source
        //      mean branch (count(unique sources) >= 2).
        self::$multiSrcGauge = Fixtures::gauge($db, ['river' => 'Confluence', 'state' => 'OR']);
        $srcA = Fixtures::source($db);
        $srcB = Fixtures::source($db);
        Fixtures::linkGaugeSource($db, self::$multiSrcGauge, $srcA);
        Fixtures::linkGaugeSource($db, self::$multiSrcGauge, $srcB);
        for ($i = 0; $i < 6; $i++) {
            $ts = gmdate('Y-m-d H:i:s', time() - $i * 900);
            Fixtures::observation($db, $srcA, ['data_type' => 'flow', 'observed_at' => $ts, 'value' => 300.0 + $i]);
            Fixtures::observation($db, $srcB, ['data_type' => 'flow', 'observed_at' => $ts, 'value' => 320.0 + $i]);
        }

        // ---- empty gauge: linked source but zero observations
        self::$emptyGauge = Fixtures::gauge($db, ['river' => 'Dry Gulch', 'state' => 'OR']);
        $emptySrc = Fixtures::source($db);
        Fixtures::linkGaugeSource($db, self::$emptyGauge, $emptySrc);

        // ---- stale gauge: flow+gauge present but all observations 5 days old,
        //      so the default-view 6h "current" check fails (primary_type = null
        //      in default view) while the explicit-window path still finds them.
        self::$staleGauge = Fixtures::gauge($db, ['river' => 'Old River', 'state' => 'OR']);
        $staleSrc = Fixtures::source($db);
        Fixtures::linkGaugeSource($db, self::$staleGauge, $staleSrc);
        for ($i = 0; $i < 4; $i++) {
            $ts = gmdate('Y-m-d H:i:s', time() - 5 * 86400 - $i * 3600);
            Fixtures::observation($db, $staleSrc, ['data_type' => 'flow', 'observed_at' => $ts, 'value' => 150.0 + $i]);
            Fixtures::observation($db, $staleSrc, ['data_type' => 'gauge', 'observed_at' => $ts, 'value' => 3.0 + $i * 0.2]);
        }

        // ---- unpaired gauge: current flow AND gauge present, but flow and
        //      gauge observations never share a (source_id, observed_at) pair,
        //      so derive_rating_lookup finds zero pairs → null. The dual path's
        //      lookup is null → falls back to two single-axis plots. Flow on
        //      the hour, gauge on the half-hour.
        self::$unpairedGauge = Fixtures::gauge($db, ['river' => 'Unpaired River', 'state' => 'OR']);
        $upSrc = Fixtures::source($db);
        Fixtures::linkGaugeSource($db, self::$unpairedGauge, $upSrc);
        for ($i = 0; $i < 4; $i++) {
            $fts = gmdate('Y-m-d H:i:s', time() - $i * 3600);
            $gts = gmdate('Y-m-d H:i:s', time() - $i * 3600 - 1800);
            Fixtures::observation($db, $upSrc, ['data_type' => 'flow', 'observed_at' => $fts, 'value' => 210.0 + $i]);
            Fixtures::observation($db, $upSrc, ['data_type' => 'gauge', 'observed_at' => $gts, 'value' => 4.0 + $i * 0.1]);
        }

        // ---- one-point gauge: a single gauge-height obs in the window → the
        //      single-axis renderer's <2-point early return fires (no plot).
        self::$onePointGauge = Fixtures::gauge($db, ['river' => 'Single Point', 'state' => 'OR']);
        $opSrc = Fixtures::source($db);
        Fixtures::linkGaugeSource($db, self::$onePointGauge, $opSrc);
        Fixtures::observation($db, $opSrc, [
            'data_type' => 'gauge',
            'observed_at' => gmdate('Y-m-d H:i:s'),
            'value' => 4.2,
        ]);
    }

    // ---- gp_resolve_window ----------------------------------------------

    public function test_resolve_window_default_uses_latest_minus_10_days(): void
    {
        [$latest_ts, $since, $until, $is_default] = gp_resolve_window($this->pdo(), self::$flowOnlyGauge, null, null);
        $this->assertTrue($is_default);
        $this->assertNull($until);
        $this->assertIsInt($latest_ts);
        // since is ~10 days before latest.
        $this->assertSame($latest_ts - 10 * 86400, strtotime($since));
    }

    public function test_resolve_window_no_data_falls_back_to_now(): void
    {
        $before = time();
        [$latest_ts, , , $is_default] = gp_resolve_window($this->pdo(), self::$emptyGauge, null, null);
        $this->assertTrue($is_default);
        // No observations → latest_ts defaults to ~now.
        $this->assertGreaterThanOrEqual($before, $latest_ts);
    }

    public function test_resolve_window_explicit_dates(): void
    {
        [, $since, $until, $is_default] = gp_resolve_window(
            $this->pdo(),
            self::$flowOnlyGauge,
            '2026-03-01',
            '2026-03-10',
        );
        $this->assertFalse($is_default);
        $this->assertSame('2026-03-01 00:00:00', $since);
        $this->assertSame('2026-03-10 23:59:59', $until);
    }

    public function test_resolve_window_empty_string_dates_are_default(): void
    {
        // Empty-string start/end should be treated as "not provided".
        [, , $until, $is_default] = gp_resolve_window($this->pdo(), self::$flowOnlyGauge, '', '');
        $this->assertTrue($is_default);
        $this->assertNull($until);
    }

    // ---- gp_render_date_form --------------------------------------------

    public function test_render_date_form_defaults_from_latest_ts(): void
    {
        $latest = mktime(0, 0, 0, 4, 15, 2026);
        $this->assertIsInt($latest);
        $html = $this->capture(fn() => gp_render_date_form(42, null, null, $latest));
        $this->assertStringContainsString('name="h" value="' . pubhash_encode(42) . '"', $html);
        $this->assertStringContainsString('type="date" name="start"', $html);
        $this->assertStringContainsString('type="date" name="end"', $html);
        // default end = latest date, default start = latest - 10 days.
        $this->assertStringContainsString('value="' . date('Y-m-d', $latest) . '"', $html);
        $this->assertStringContainsString('value="' . date('Y-m-d', $latest - 10 * 86400) . '"', $html);
        $this->assertStringContainsString('<button type="submit"', $html);
    }

    public function test_render_date_form_uses_supplied_values_and_links(): void
    {
        $html = $this->capture(fn() => gp_render_date_form(
            7,
            '2026-02-01',
            '2026-02-15',
            time(),
            [['label' => 'CSV', 'url' => '/data.csv?id=7&x=1']],
        ));
        $this->assertStringContainsString('value="2026-02-01"', $html);
        $this->assertStringContainsString('value="2026-02-15"', $html);
        // extra link is rendered with htmlspecialchars-escaped url.
        $this->assertStringContainsString('CSV', $html);
        $this->assertStringContainsString('/data.csv?id=7&amp;x=1', $html);
    }

    // ---- gp_render_plots: dual rating-curve path ------------------------

    public function test_render_plots_default_view_dual_rating_plot(): void
    {
        [$latest_ts, $since, $until, $is_default] = gp_resolve_window($this->pdo(), self::$dualGauge, null, null);
        $html = $this->capture(fn() => gp_render_plots(
            $this->pdo(),
            self::$dualGauge,
            'Dual River',
            $since,
            $until,
            $latest_ts,
            $is_default,
        ));
        $this->assertStringContainsString('plot-container', $html);
        // Dual plot carries the combined title + the gauge-height right axis.
        $this->assertStringContainsString('Flow (CFS) / Gage Height', $html);
        $this->assertStringContainsString('Gage Height (Ft)', $html);
        // The data-series JSON is HTML-escaped inside the attribute.
        $this->assertStringContainsString('&quot;kind&quot;:&quot;dual&quot;', $html);
    }

    public function test_render_plots_dual_plot_with_class_range_bands(): void
    {
        // class_range in flow units → bands project straight onto the flow axis.
        $class_range = [
            'low' => 150.0, 'low_data_type' => 'flow',
            'high' => 400.0, 'high_data_type' => 'flow',
        ];
        [$latest_ts, $since, $until, $is_default] = gp_resolve_window($this->pdo(), self::$dualGauge, null, null);
        $html = $this->capture(fn() => gp_render_plots(
            $this->pdo(),
            self::$dualGauge,
            'Dual River',
            $since,
            $until,
            $latest_ts,
            $is_default,
            $class_range,
        ));
        // Band rects are emitted in the dual plot.
        $this->assertStringContainsString('fill-opacity="0.12"', $html);
        $this->assertStringContainsString('&quot;kind&quot;:&quot;dual&quot;', $html);
    }

    public function test_render_plots_dual_plot_with_gauge_unit_bands(): void
    {
        // Bounds expressed in GAUGE units on a FLOW axis → must be projected
        // through the rating lookup (rate_gauge_to_flow). Exercises the
        // cross-unit branch of _gp_bands_for_axis with a non-null lookup.
        $class_range = [
            'low' => 3.5, 'low_data_type' => 'gauge',
            'high' => 4.5, 'high_data_type' => 'gauge',
        ];
        [$latest_ts, $since, $until, $is_default] = gp_resolve_window($this->pdo(), self::$dualGauge, null, null);
        $html = $this->capture(fn() => gp_render_plots(
            $this->pdo(),
            self::$dualGauge,
            'Dual River',
            $since,
            $until,
            $latest_ts,
            $is_default,
            $class_range,
        ));
        $this->assertStringContainsString('fill-opacity="0.12"', $html);
    }

    public function test_render_plots_primary_and_gauge_without_pairs_falls_back_to_single(): void
    {
        // Current flow + gauge both present, but no paired (source,time) rows →
        // derive_rating_lookup returns null → the dual branch's else falls back
        // to TWO single-axis plots (flow, then gauge). Covers the in-branch
        // single-axis fallback (the lookup-null arm).
        [$latest_ts, $since, $until, $is_default] = gp_resolve_window($this->pdo(), self::$unpairedGauge, null, null);
        $html = $this->capture(fn() => gp_render_plots(
            $this->pdo(),
            self::$unpairedGauge,
            'Unpaired River',
            $since,
            $until,
            $latest_ts,
            $is_default,
        ));
        // Not a dual plot…
        $this->assertStringNotContainsString('&quot;kind&quot;:&quot;dual&quot;', $html);
        // …but BOTH single-axis plots render (flow + gauge height).
        $this->assertStringContainsString('Flow (CFS)', $html);
        $this->assertStringContainsString('Gage Height (Ft)', $html);
        // Two single-axis SVGs → two data-series single payloads.
        $this->assertSame(2, substr_count($html, '&quot;kind&quot;:&quot;single&quot;'));
    }

    public function test_render_plots_single_point_series_renders_nothing(): void
    {
        // Only one gauge-height observation in the window → the single-axis
        // renderer's `count($times) < 2` early return fires, emitting no plot.
        [$latest_ts, $since, $until, $is_default] = gp_resolve_window($this->pdo(), self::$onePointGauge, null, null);
        $html = $this->capture(fn() => gp_render_plots(
            $this->pdo(),
            self::$onePointGauge,
            'Single Point',
            $since,
            $until,
            $latest_ts,
            $is_default,
        ));
        $this->assertSame('', $html);
    }

    // ---- gp_render_plots: single-axis fallback (explicit window, stale gauge)

    public function test_render_plots_explicit_window_single_axis_flow_and_gauge(): void
    {
        // Explicit window over the stale gauge's data: primary=flow (has_flow),
        // has_gauge true, but the rating lookup uses a 60-day lookback from
        // latest_ts — paired points exist so the dual path may fire. Use a gauge
        // whose paired points are too sparse to bin: the flowOnly gauge has no
        // gauge series, forcing the single-axis flow-only branch instead.
        $start = gmdate('Y-m-d', time() - 11 * 86400);
        $end = gmdate('Y-m-d', time() + 1);
        [$latest_ts, $since, $until, $is_default] =
            gp_resolve_window($this->pdo(), self::$flowOnlyGauge, $start, $end);
        $html = $this->capture(fn() => gp_render_plots(
            $this->pdo(),
            self::$flowOnlyGauge,
            'Flow River',
            $since,
            $until,
            $latest_ts,
            $is_default,
        ));
        $this->assertStringContainsString('plot-container', $html);
        $this->assertStringContainsString('Flow (CFS)', $html);
        // Single-axis plot, not dual (JSON HTML-escaped in the attribute).
        $this->assertStringContainsString('&quot;kind&quot;:&quot;single&quot;', $html);
        $this->assertStringNotContainsString('&quot;kind&quot;:&quot;dual&quot;', $html);
    }

    public function test_render_plots_default_view_stale_data_renders_nothing(): void
    {
        // Stale gauge: flow+gauge present but > 6h old. In default view the
        // "current within 6h" check fails for both flow and inflow, so
        // primary_type is null and has_gauge is also false in the 10-day window
        // (data is 5 days old, still inside 10 days → gauge plot renders).
        // Assert it does NOT render a flow/dual plot but DOES render the gauge.
        [$latest_ts, $since, $until, $is_default] = gp_resolve_window($this->pdo(), self::$staleGauge, null, null);
        $html = $this->capture(fn() => gp_render_plots(
            $this->pdo(),
            self::$staleGauge,
            'Old River',
            $since,
            $until,
            $latest_ts,
            $is_default,
        ));
        // No flow primary (stale) → falls to gauge-only single-axis.
        $this->assertStringNotContainsString('"kind":"dual"', $html);
        $this->assertStringContainsString('Gage Height (Ft)', $html);
    }

    public function test_render_plots_gauge_only(): void
    {
        [$latest_ts, $since, $until, $is_default] = gp_resolve_window($this->pdo(), self::$gaugeOnlyGauge, null, null);
        $html = $this->capture(fn() => gp_render_plots(
            $this->pdo(),
            self::$gaugeOnlyGauge,
            'Stage River',
            $since,
            $until,
            $latest_ts,
            $is_default,
        ));
        $this->assertStringContainsString('Gage Height (Ft)', $html);
        $this->assertStringContainsString('&quot;kind&quot;:&quot;single&quot;', $html);
        $this->assertStringNotContainsString('Flow (CFS)', $html);
    }

    public function test_render_plots_gauge_only_with_gauge_bands(): void
    {
        // gauge-axis plot + gauge-unit bands → straight projection, no lookup.
        $class_range = [
            'low' => 4.1, 'low_data_type' => 'gauge',
            'high' => 4.3, 'high_data_type' => 'gauge',
        ];
        [$latest_ts, $since, $until, $is_default] = gp_resolve_window($this->pdo(), self::$gaugeOnlyGauge, null, null);
        $html = $this->capture(fn() => gp_render_plots(
            $this->pdo(),
            self::$gaugeOnlyGauge,
            'Stage River',
            $since,
            $until,
            $latest_ts,
            $is_default,
            $class_range,
        ));
        $this->assertStringContainsString('fill-opacity="0.12"', $html);
    }

    public function test_render_plots_inflow_primary_and_temperature(): void
    {
        [$latest_ts, $since, $until, $is_default] = gp_resolve_window($this->pdo(), self::$inflowGauge, null, null);
        $html = $this->capture(fn() => gp_render_plots(
            $this->pdo(),
            self::$inflowGauge,
            'Reservoir',
            $since,
            $until,
            $latest_ts,
            $is_default,
        ));
        // inflow becomes the primary single-axis series; temperature plot appended.
        $this->assertStringContainsString('Inflow (CFS)', $html);
        $this->assertStringContainsString('Temperature (F)', $html);
    }

    public function test_render_plots_multi_source_aggregation(): void
    {
        // Two sources reporting flow → _gp_fetch_series runs despike +
        // cross-source mean before plotting. Still produces a flow plot.
        [$latest_ts, $since, $until, $is_default] = gp_resolve_window($this->pdo(), self::$multiSrcGauge, null, null);
        $html = $this->capture(fn() => gp_render_plots(
            $this->pdo(),
            self::$multiSrcGauge,
            'Confluence',
            $since,
            $until,
            $latest_ts,
            $is_default,
        ));
        $this->assertStringContainsString('Flow (CFS)', $html);
        $this->assertStringContainsString('&quot;kind&quot;:&quot;single&quot;', $html);
    }

    public function test_render_plots_empty_gauge_emits_nothing(): void
    {
        [$latest_ts, $since, $until, $is_default] = gp_resolve_window($this->pdo(), self::$emptyGauge, null, null);
        $html = $this->capture(fn() => gp_render_plots(
            $this->pdo(),
            self::$emptyGauge,
            'Dry Gulch',
            $since,
            $until,
            $latest_ts,
            $is_default,
        ));
        $this->assertSame('', $html);
    }

    // ---- gauge_plots_data.php helpers (direct) --------------------------

    public function test_has_obs_with_and_without_until(): void
    {
        $now = gmdate('Y-m-d H:i:s');
        $past = gmdate('Y-m-d H:i:s', time() - 10 * 86400);
        // bounded window
        $this->assertTrue(_gp_has_obs($this->pdo(), self::$flowOnlyGauge, 'flow', $past, $now));
        // unbounded (until = null)
        $this->assertTrue(_gp_has_obs($this->pdo(), self::$flowOnlyGauge, 'flow', $past, null));
        // wrong type
        $this->assertFalse(_gp_has_obs($this->pdo(), self::$flowOnlyGauge, 'gauge', $past, null));
        // empty gauge
        $this->assertFalse(_gp_has_obs($this->pdo(), self::$emptyGauge, 'flow', $past, null));
    }

    public function test_has_current_obs_true_for_fresh_and_false_for_stale(): void
    {
        // dual gauge's flow is timestamped within hours → current within 6h.
        $this->assertTrue(_gp_has_current_obs($this->pdo(), self::$dualGauge, 'flow', 6));
        // stale gauge's flow is 5 days old → not current within 6h.
        $this->assertFalse(_gp_has_current_obs($this->pdo(), self::$staleGauge, 'flow', 6));
        // ...but it IS current within a 200-day window.
        $this->assertTrue(_gp_has_current_obs($this->pdo(), self::$staleGauge, 'flow', 24 * 200));
        // empty gauge → false (no rows).
        $this->assertFalse(_gp_has_current_obs($this->pdo(), self::$emptyGauge, 'flow', 6));
    }

    public function test_fetch_series_single_source_verbatim(): void
    {
        $past = gmdate('Y-m-d H:i:s', time() - 10 * 86400);
        [$times, $values] = _gp_fetch_series($this->pdo(), self::$flowOnlyGauge, 'flow', $past, null);
        $this->assertCount(4, $times);
        $this->assertCount(4, $values);
        // values are floats; times are sorted ascending (ORDER BY observed_at).
        $this->assertContainsOnly('float', $values);
        $sorted = $times;
        sort($sorted);
        $this->assertSame($sorted, $times);
    }

    public function test_fetch_series_multi_source_aggregates(): void
    {
        $past = gmdate('Y-m-d H:i:s', time() - 10 * 86400);
        $now = gmdate('Y-m-d H:i:s', time() + 60);
        // bounded form, two sources → cross-source mean path.
        [$times, $values] = _gp_fetch_series($this->pdo(), self::$multiSrcGauge, 'flow', $past, $now);
        $this->assertNotEmpty($times);
        $this->assertSameSize($times, $values);
        // Aggregated values lie between the two source levels (300s and 320s).
        foreach ($values as $v) {
            $this->assertGreaterThanOrEqual(300.0, $v);
            $this->assertLessThanOrEqual(330.0, $v);
        }
    }

    public function test_fetch_series_empty_returns_empty_arrays(): void
    {
        $past = gmdate('Y-m-d H:i:s', time() - 10 * 86400);
        [$times, $values] = _gp_fetch_series($this->pdo(), self::$emptyGauge, 'flow', $past, null);
        $this->assertSame([], $times);
        $this->assertSame([], $values);
    }

    // ---- _gp_bands_for_axis (direct) ------------------------------------

    public function test_bands_for_axis_null_class_range(): void
    {
        $this->assertNull(_gp_bands_for_axis(null, 'flow'));
    }

    public function test_bands_for_axis_same_unit_passthrough(): void
    {
        $bands = _gp_bands_for_axis(
            ['low' => 100.0, 'low_data_type' => 'flow', 'high' => 500.0, 'high_data_type' => 'flow'],
            'flow',
        );
        $this->assertSame(['low' => 100.0, 'high' => 500.0], $bands);
    }

    public function test_bands_for_axis_missing_data_type_defaults_to_flow(): void
    {
        // No low_data_type/high_data_type keys → treated as 'flow'; on a flow
        // axis that's a straight pass-through.
        $bands = _gp_bands_for_axis(['low' => 120.0, 'high' => 480.0], 'flow');
        $this->assertSame(['low' => 120.0, 'high' => 480.0], $bands);
    }

    public function test_bands_for_axis_all_null_bounds_returns_null(): void
    {
        $this->assertNull(_gp_bands_for_axis(['low' => null, 'high' => null], 'flow'));
    }

    public function test_bands_for_axis_cross_unit_without_lookup_returns_null(): void
    {
        // Gauge-unit bounds on a flow axis with no rating lookup → unprojectable,
        // both bounds null → returns null.
        $bands = _gp_bands_for_axis(
            ['low' => 3.0, 'low_data_type' => 'gauge', 'high' => 5.0, 'high_data_type' => 'gauge'],
            'flow',
            null,
        );
        $this->assertNull($bands);
    }

    public function test_bands_for_axis_cross_unit_with_lookup_projects(): void
    {
        // Gauge bounds projected to flow via a 3-point rating lookup.
        $lookup = [[3.0, 100.0], [4.0, 200.0], [5.0, 400.0]];
        $bands = _gp_bands_for_axis(
            ['low' => 3.5, 'low_data_type' => 'gauge', 'high' => 4.5, 'high_data_type' => 'gauge'],
            'flow',
            $lookup,
        );
        $this->assertNotNull($bands);
        $this->assertSame(150.0, $bands['low']);   // interp 3.5 → 150
        $this->assertSame(300.0, $bands['high']);  // interp 4.5 → 300
    }

    public function test_bands_for_axis_flow_bounds_on_gauge_axis_projects(): void
    {
        // Inverse direction: flow bounds projected onto a gauge axis.
        $lookup = [[3.0, 100.0], [4.0, 200.0], [5.0, 400.0]];
        $bands = _gp_bands_for_axis(
            ['low' => 150.0, 'low_data_type' => 'flow', 'high' => 300.0, 'high_data_type' => 'flow'],
            'gauge',
            $lookup,
        );
        $this->assertNotNull($bands);
        $this->assertSame(3.5, $bands['low']);   // flow 150 → gauge 3.5
        $this->assertSame(4.5, $bands['high']);  // flow 300 → gauge 4.5
    }

    public function test_bands_for_axis_inflow_axis_is_flow_like(): void
    {
        // 'inflow' axis is treated as flow-like; flow-typed bounds pass through.
        $bands = _gp_bands_for_axis(
            ['low' => 50.0, 'low_data_type' => 'inflow', 'high' => 90.0, 'high_data_type' => 'inflow'],
            'inflow',
        );
        $this->assertSame(['low' => 50.0, 'high' => 90.0], $bands);
    }

    public function test_bands_for_axis_one_sided_low_only(): void
    {
        $bands = _gp_bands_for_axis(['low' => 100.0, 'low_data_type' => 'flow'], 'flow');
        $this->assertSame(['low' => 100.0, 'high' => null], $bands);
    }
}
