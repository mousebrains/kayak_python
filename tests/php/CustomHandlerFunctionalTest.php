<?php

declare(strict_types=1);

require_once __DIR__ . '/FunctionalTestCase.php';
require_once __DIR__ . '/Fixtures.php';
require_once __DIR__ . '/../../php/includes/db.php';
require_once __DIR__ . '/../../php/includes/header.php';
require_once __DIR__ . '/../../php/includes/footer.php';
require_once __DIR__ . '/../../php/includes/custom_handler.php';

/**
 * In-process functional coverage for custom_handler.php
 * (handle_custom_levels + its private loaders/renderers).
 *
 * Drives the full reach-row LEFT JOIN, the URL-order reorder, the
 * class/tier rollup, the sparkline batch fetch+downsample, and every
 * visible branch in the header filter-bar and the 9-column table:
 *   - status pill (low / okay / high / unknown) from reach_class ranges
 *   - flow-delta words (stable / rising / falling)
 *   - state + watershed filter rows (>1 state shows the State group)
 *   - sparkline SVG (>= 3 points, after downsample from > 60)
 *   - empty-ids edge (loaders handle `IN ()` without crashing)
 */
final class CustomHandlerFunctionalTest extends FunctionalTestCase
{
    /** reach with okay flow (in class range), OR, basin Willamette, rising */
    private static int $reachOkay = 0;
    /** reach with low flow (below range), WA, basin Columbia, falling */
    private static int $reachLow = 0;
    /** reach with high flow (above range), OR, basin Willamette, stable */
    private static int $reachHigh = 0;
    /** reach with a gauge but no class range → status NULL (unknown) */
    private static int $reachUnknown = 0;
    /** reach with no gauge at all → blank cells, no crash */
    private static int $reachNoGauge = 0;
    /** gauge backing $reachOkay, also carries a 70-point sparkline source */
    private static int $gaugeOkay = 0;

    /** Look up a seeded state id by two-letter abbreviation. */
    private static function stateId(PDO $db, string $abbrev): int
    {
        $stmt = $db->prepare('SELECT id FROM state WHERE abbreviation = ?');
        $stmt->execute([$abbrev]);
        return (int) $stmt->fetchColumn();
    }

    protected static function seedDatabase(PDO $db): void
    {
        // init-db already seeds the 12 states; reuse OR + WA so the State
        // filter group (count > 1) renders without a UNIQUE collision.
        $orId = self::stateId($db, 'OR');
        $waId = self::stateId($db, 'WA');

        // --- okay reach: flow 800 inside [500, 2000]; rising (delta +5) ---
        self::$gaugeOkay = Fixtures::gauge($db, ['location' => 'Estacada', 'state' => 'OR']);
        $srcOkay = Fixtures::source($db);
        Fixtures::linkGaugeSource($db, self::$gaugeOkay, $srcOkay);
        Fixtures::latestGaugeObservation($db, self::$gaugeOkay, [
            'data_type' => 'flow', 'value' => 800.0, 'delta_per_hour' => 5.0,
        ]);
        Fixtures::latestGaugeObservation($db, self::$gaugeOkay, [
            'data_type' => 'gauge', 'value' => 4.25,
        ]);
        Fixtures::latestGaugeObservation($db, self::$gaugeOkay, [
            'data_type' => 'temperature', 'value' => 52.0,
        ]);
        self::$reachOkay = Fixtures::reach($db, [
            'name' => 'Okay Reach', 'display_name' => 'Okay Reach',
            'basin' => 'Willamette', 'gauge_id' => self::$gaugeOkay, 'no_show' => 0,
        ]);
        Fixtures::reachClass($db, self::$reachOkay, [
            'name' => 'III', 'low' => 500.0, 'high' => 2000.0,
            'low_data_type' => 'flow', 'high_data_type' => 'flow',
        ]);
        Fixtures::linkReachState($db, self::$reachOkay, $orId);
        // > 60 flow observations so _load_custom_sparklines downsamples to ~60+1.
        for ($i = 0; $i < 70; $i++) {
            Fixtures::observation($db, $srcOkay, [
                'data_type' => 'flow',
                'value' => 700.0 + $i,
                'observed_at' => date('Y-m-d H:i:s', time() - (70 - $i) * 1800),
            ]);
        }

        // --- low reach: flow 100 below [500, 2000]; falling (delta -3) ---
        $gaugeLow = Fixtures::gauge($db, ['location' => 'Cascadia', 'state' => 'WA']);
        Fixtures::latestGaugeObservation($db, $gaugeLow, [
            'data_type' => 'flow', 'value' => 100.0, 'delta_per_hour' => -3.0,
        ]);
        self::$reachLow = Fixtures::reach($db, [
            'name' => 'Low Reach', 'display_name' => 'Low Reach',
            'basin' => 'Columbia', 'gauge_id' => $gaugeLow, 'no_show' => 0,
        ]);
        Fixtures::reachClass($db, self::$reachLow, [
            'name' => 'IV', 'low' => 500.0, 'high' => 2000.0,
            'low_data_type' => 'flow', 'high_data_type' => 'flow',
        ]);
        Fixtures::linkReachState($db, self::$reachLow, $waId);

        // --- high reach: flow 5000 above [500, 2000]; stable (delta 0.1) ---
        $gaugeHigh = Fixtures::gauge($db, ['location' => 'Gorge', 'state' => 'OR']);
        Fixtures::latestGaugeObservation($db, $gaugeHigh, [
            'data_type' => 'flow', 'value' => 5000.0, 'delta_per_hour' => 0.1,
        ]);
        // Two class names on one reach exercises the comma-join + tier merge.
        self::$reachHigh = Fixtures::reach($db, [
            'name' => 'High Reach', 'display_name' => 'High Reach',
            'basin' => 'Willamette', 'gauge_id' => $gaugeHigh, 'no_show' => 0,
        ]);
        Fixtures::reachClass($db, self::$reachHigh, [
            'name' => 'II-III', 'low' => 500.0, 'high' => 2000.0,
            'low_data_type' => 'flow', 'high_data_type' => 'flow',
        ]);
        Fixtures::reachClass($db, self::$reachHigh, ['name' => 'V']);
        Fixtures::linkReachState($db, self::$reachHigh, $orId);

        // --- unknown reach: gauge + flow but NO class range → status NULL ---
        $gaugeUnknown = Fixtures::gauge($db, ['location' => 'Nowhere']);
        Fixtures::latestGaugeObservation($db, $gaugeUnknown, [
            'data_type' => 'flow', 'value' => 300.0,
        ]);
        self::$reachUnknown = Fixtures::reach($db, [
            'name' => 'Unknown Reach', 'display_name' => 'Unknown Reach',
            'gauge_id' => $gaugeUnknown, 'no_show' => 0,
        ]);
        // No reach_class row, no reach_state row → exercises the NULL-status
        // and empty-tier ('?') branches plus the "(none)" basin label.

        // --- no-gauge reach: blank cells, no sparkline lookup ---
        self::$reachNoGauge = Fixtures::reach($db, [
            'name' => 'No-Gauge Reach', 'display_name' => 'No-Gauge Reach',
            'basin' => 'Willamette', 'no_show' => 0,
        ]);
        Fixtures::linkReachState($db, self::$reachNoGauge, $orId);
    }

    public function testRendersAllStatusPills(): void
    {
        $ids = [self::$reachOkay, self::$reachLow, self::$reachHigh, self::$reachUnknown];
        $html = $this->capture(fn() => handle_custom_levels($this->pdo(), $ids));

        $this->assertStringContainsString('<table class="levels">', $html);
        $this->assertStringContainsString('Okay Reach', $html);
        $this->assertStringContainsString('Low Reach', $html);
        $this->assertStringContainsString('High Reach', $html);

        // Status filter values come from the class-range CASE: low/okay/high.
        $this->assertStringContainsString('data-status="low"', $html);
        $this->assertStringContainsString('data-status="okay"', $html);
        $this->assertStringContainsString('data-status="high"', $html);
        // Unknown reach (gauge+flow but no class range) → 'unknown'.
        $this->assertStringContainsString('data-status="unknown"', $html);

        // Flow-delta words: rising (+5), falling (-3), stable (0.1).
        $this->assertStringContainsString('class="rising"', $html);
        $this->assertStringContainsString('class="falling"', $html);
        $this->assertStringContainsString('class="stable"', $html);

        // "4 reaches" count line (plural).
        $this->assertStringContainsString('4 reach', $html);
    }

    public function testFilterBarShowsStateAndWatershedRows(): void
    {
        $ids = [self::$reachOkay, self::$reachLow, self::$reachHigh];
        $html = $this->capture(fn() => handle_custom_levels($this->pdo(), $ids));

        // >1 state present → State filter group rendered. The reach-row SQL's
        // `state` subquery returns state.name, so pills carry the full name.
        $this->assertStringContainsString('data-group="state"', $html);
        $this->assertStringContainsString('value="Oregon"', $html);
        $this->assertStringContainsString('value="Washington"', $html);

        // Watershed group always rendered; both basins present.
        $this->assertStringContainsString('data-group="basin"', $html);
        $this->assertStringContainsString('value="Willamette"', $html);
        $this->assertStringContainsString('value="Columbia"', $html);

        // Status + Class (tier) filter groups.
        $this->assertStringContainsString('data-group="status"', $html);
        $this->assertStringContainsString('data-group="tier"', $html);

        // Edit-selection link carries resolved ids.
        $this->assertStringContainsString('/picker.php?ids=', $html);
    }

    public function testSingleStateOmitsStateFilterGroup(): void
    {
        // Only OR reaches → State group (which needs count > 1) is omitted,
        // and the basin "(none)" label fires for the unknown reach.
        $ids = [self::$reachOkay, self::$reachHigh, self::$reachUnknown];
        $html = $this->capture(fn() => handle_custom_levels($this->pdo(), $ids));

        $this->assertStringNotContainsString('data-group="state"', $html);
        // Unknown reach has no basin → "(none)" pill label.
        $this->assertStringContainsString('(none)', $html);
        // Its empty tier set → '?' class pill.
        $this->assertStringContainsString('data-group="tier"', $html);
        $this->assertStringContainsString('value="?"', $html);
    }

    public function testReorderByUrlPosition(): void
    {
        // Request high, then okay — reorder must preserve URL order even
        // though okay has the lower id.
        $ids = [self::$reachHigh, self::$reachOkay];
        $html = $this->capture(fn() => handle_custom_levels($this->pdo(), $ids));

        $posHigh = strpos($html, 'High Reach');
        $posOkay = strpos($html, 'Okay Reach');
        $this->assertNotFalse($posHigh);
        $this->assertNotFalse($posOkay);
        $this->assertLessThan($posOkay, $posHigh, 'URL order (high before okay) not preserved');
    }

    public function testGaugedReachShowsReadingsAndSparkline(): void
    {
        $html = $this->capture(fn() => handle_custom_levels($this->pdo(), [self::$reachOkay]));

        // Flow int + plot link, gage 2dp, temp 0dp.
        $this->assertStringContainsString('/plot.php?type=flow&id=' . self::$reachOkay, $html);
        $this->assertStringContainsString('>800<', $html);
        $this->assertStringContainsString('/plot.php?type=gage&id=' . self::$reachOkay, $html);
        $this->assertStringContainsString('>4.25<', $html);
        $this->assertStringContainsString('/plot.php?type=temp&id=' . self::$reachOkay, $html);
        $this->assertStringContainsString('>52<', $html);

        // 70 obs downsampled → an inline SVG sparkline.
        $this->assertStringContainsString('<svg class="spark"', $html);
        $this->assertStringContainsString('<polyline', $html);

        // Best-available <time> element from flow_time.
        $this->assertStringContainsString('<time datetime="', $html);

        // "1 reach" singular (no trailing "es").
        $this->assertStringContainsString('1 reach<', $html);
    }

    public function testNoGaugeReachRendersWithoutCrash(): void
    {
        $html = $this->capture(fn() => handle_custom_levels($this->pdo(), [self::$reachNoGauge]));

        $this->assertStringContainsString('No-Gauge Reach', $html);
        // No gauge → no plot links, no sparkline.
        $this->assertStringNotContainsString('/plot.php?type=flow', $html);
        $this->assertStringNotContainsString('<svg class="spark"', $html);
    }

    public function testEmptyIdsRendersEmptyTable(): void
    {
        // The shim redirects on empty ids; the handler itself must still
        // survive an empty list (IN () under PDO/SQLite is a no-op match).
        $html = $this->capture(fn() => handle_custom_levels($this->pdo(), []));

        $this->assertStringContainsString('<table class="levels">', $html);
        // "0 reaches" plural; no edit-selection ids query string.
        $this->assertStringContainsString('0 reach', $html);
        $this->assertStringContainsString('href="/picker.php"', $html);
    }

    public function testBuildSparklineHelperEdges(): void
    {
        // < 3 points → empty string.
        $this->assertSame('', _build_custom_sparkline([['ts' => 1, 'v' => 1.0]]));

        // Flat series (zero x/y span) still produces a polyline (range guard).
        $flat = [
            ['ts' => 100, 'v' => 5.0],
            ['ts' => 100, 'v' => 5.0],
            ['ts' => 100, 'v' => 5.0],
        ];
        $svg = _build_custom_sparkline($flat);
        $this->assertStringContainsString('<polyline', $svg);
    }
}
