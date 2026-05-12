<?php

declare(strict_types=1);

require_once __DIR__ . '/IntegrationTestCase.php';

/**
 * Baseline integration tests for custom_gauges.php (Phase 5.C.1 of
 * php_layer_split). Sister page to custom.php — same `?ids=CSV`
 * shape, but gauge-IDs instead of reach-IDs, and empty/missing
 * redirects to /gauge_picker.php rather than /picker.php.
 *
 * Covers:
 *  - Missing/empty ids → 302 to /gauge_picker.php
 *  - Single gauge: renders the gauge in the table
 *  - Multi-gauge in URL order
 *  - Gauge with readings: flow/gage/temp cells populated
 *  - Gauge without readings: cells blank, no crash
 *  - Status rollup: gauge with okay-status reach gets the okay label
 *
 * Seed: 2 gauges (id=7001 with reach + flow obs + class threshold so
 * status rollup hits, 7002 minimal/no-readings).
 */
final class CustomGaugesIntegrationTest extends IntegrationTestCase
{
    private const GAUGE_WITH_READINGS = 7001;
    private const GAUGE_NO_READINGS = 7002;
    private const REACH_ID = 7501;

    protected static function seedDatabase(PDO $db): void
    {
        // Gauge 1: with river/location/state + readings + an associated
        // reach with class thresholds so the status rollup CASE fires.
        $db->prepare(
            "INSERT INTO gauge (id, name, display_name, river, location, state, huc)
             VALUES (?, ?, ?, ?, ?, ?, ?)"
        )->execute([
            self::GAUGE_WITH_READINGS,
            'CUSTGAUGE_R',
            'Custom Gauges Test (readings)',
            'Sandy',
            'Marmot',
            'OR',
            '17090010',
        ]);
        $db->prepare(
            "INSERT INTO gauge (id, name, display_name) VALUES (?, ?, ?)"
        )->execute([
            self::GAUGE_NO_READINGS,
            'CUSTGAUGE_E',
            'Custom Gauges Test (empty)',
        ]);

        foreach ([['flow', 750.0], ['gauge', 3.5], ['temperature', 48.0]] as [$dt, $v]) {
            $db->prepare(
                "INSERT INTO latest_gauge_observation
                    (gauge_id, data_type, value, observed_at)
                 VALUES (?, ?, ?, datetime('now', '-1 hour'))"
            )->execute([self::GAUGE_WITH_READINGS, $dt, $v]);
        }

        // Reach + class threshold so status rollup classifies 750 cfs as 'okay'.
        $db->prepare(
            'INSERT INTO reach (id, name, display_name, river, sort_name, gauge_id, no_show)
             VALUES (?, ?, ?, ?, ?, ?, ?)'
        )->execute([
            self::REACH_ID, 'Custom Gauges Reach', 'Custom Gauges Reach',
            'Sandy', 'custom gauges reach', self::GAUGE_WITH_READINGS, 0,
        ]);
        $db->prepare(
            'INSERT INTO reach_class (reach_id, name, low, high, low_data_type, high_data_type)
             VALUES (?, ?, ?, ?, ?, ?)'
        )->execute([self::REACH_ID, 'III', 500.0, 2000.0, 'flow', 'flow']);
    }

    public function testMissingIdsRedirectsToGaugePicker(): void
    {
        $resp = $this->request('/custom_gauges.php');

        $this->assertSame(302, $resp['status']);
        $this->assertSame('/gauge_picker.php', $resp['headers']['location'] ?? '');
    }

    public function testInvalidIdsRedirectsToGaugePicker(): void
    {
        $resp = $this->request('/custom_gauges.php', ['ids' => 'foo,-1,0']);

        $this->assertSame(302, $resp['status']);
        $this->assertSame('/gauge_picker.php', $resp['headers']['location'] ?? '');
    }

    public function testSingleGaugeRenders(): void
    {
        $resp = $this->request(
            '/custom_gauges.php',
            ['ids' => (string)self::GAUGE_WITH_READINGS],
        );

        $this->assertSame(200, $resp['status']);
        $this->assertResponseContains(
            $resp['body'],
            'Sandy',                      // river column
            'Marmot',                     // location column
            '1 gauge',                    // count line
            '<table class="levels">',
        );
        $this->assertNoBareInlineScript($resp['body']);
    }

    public function testMultiGaugeRendersInUrlOrder(): void
    {
        // Reverse-id order; URL order should put empty (7002) before
        // readings (7001).
        $resp = $this->request('/custom_gauges.php', [
            'ids' => implode(',', [self::GAUGE_NO_READINGS, self::GAUGE_WITH_READINGS]),
        ]);

        $this->assertSame(200, $resp['status']);
        $this->assertResponseContains($resp['body'], '2 gauges');

        // The river column renders COALESCE(g.river, g.name) — for the
        // empty seed gauge (no river column), that resolves to the
        // canonical `name` 'CUSTGAUGE_E', not the display_name.
        $pos_empty = strpos($resp['body'], 'CUSTGAUGE_E');
        $pos_readings = strpos($resp['body'], 'Sandy');
        $this->assertNotFalse($pos_empty);
        $this->assertNotFalse($pos_readings);
        $this->assertLessThan($pos_readings, $pos_empty,
            'URL order broken: empty gauge should appear before readings gauge');
    }

    public function testGaugeReadingsRender(): void
    {
        $resp = $this->request(
            '/custom_gauges.php',
            ['ids' => (string)self::GAUGE_WITH_READINGS],
        );

        $this->assertSame(200, $resp['status']);
        // Flow as int: 750 → ">750<"
        $this->assertStringContainsString('>750<', $resp['body']);
        // Gage at 1 decimal: 3.5 → ">3.5<"
        $this->assertStringContainsString('>3.5<', $resp['body']);
        // Temp at 1 decimal: 48 → ">48.0<"
        $this->assertStringContainsString('>48.0<', $resp['body']);
        // Status rollup: 750 is in [500, 2000] → 'okay'
        $this->assertStringContainsString('level-okay', $resp['body']);
    }

    public function testGaugeNoReadingsRendersBlankCells(): void
    {
        $resp = $this->request(
            '/custom_gauges.php',
            ['ids' => (string)self::GAUGE_NO_READINGS],
        );

        $this->assertSame(200, $resp['status']);
        $this->assertResponseContains(
            $resp['body'],
            // SQL pulls COALESCE(g.river, g.name) AS river; empty seed
            // gauge has no river, so the canonical name renders.
            'CUSTGAUGE_E',
            '1 gauge',
        );
        // No status badge for a gauge with no reaches.
        $this->assertStringNotContainsString('level-okay', $resp['body']);
        $this->assertStringNotContainsString('level-low', $resp['body']);
        $this->assertStringNotContainsString('level-high', $resp['body']);
    }
}
