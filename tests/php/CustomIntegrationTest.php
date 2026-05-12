<?php

declare(strict_types=1);

require_once __DIR__ . '/IntegrationTestCase.php';

/**
 * Baseline integration tests for custom.php (Phase 5.C.1 of
 * php_layer_split). Single-mode entry point — `?ids=CSV` of reach
 * IDs renders a custom levels table; empty or missing ids redirects
 * to /picker.php.
 *
 * Covers:
 *  - Missing/empty ids → 302 to /picker.php
 *  - Invalid ids (negative, non-numeric) filtered → 302 (effectively
 *    empty)
 *  - Single reach: renders the reach in the table with display name
 *  - Multi-reach in URL order: reaches appear in CSV order, not DB
 *    sort_name order (drag-reorder contract from picker.php)
 *  - With gauge readings: flow/gage/temp cells populated
 *  - Without gauge: cells blank, no crash
 *
 * Seed: 3 reaches (id=6001 gauged with readings, 6002 gauged no
 * readings, 6003 no gauge), 1 gauge with flow + gage + temperature
 * latest observations.
 */
final class CustomIntegrationTest extends IntegrationTestCase
{
    private const REACH_GAUGED_WITH_READINGS = 6001;
    private const REACH_GAUGED_NO_READINGS = 6002;
    private const REACH_NO_GAUGE = 6003;
    private const GAUGE_WITH_READINGS = 6501;
    private const GAUGE_NO_READINGS = 6502;

    protected static function seedDatabase(PDO $db): void
    {
        // Two gauges; one gets latest_gauge_observation rows, the other
        // doesn't (covers the readings-vs-no-readings branches in the
        // big LEFT JOIN).
        $db->prepare(
            'INSERT INTO gauge (id, name, display_name, location) VALUES (?, ?, ?, ?)'
        )->execute([
            self::GAUGE_WITH_READINGS, 'CUSTOM_GAUGE_R', 'Custom Test Gauge (readings)', 'Estacada',
        ]);
        $db->prepare(
            'INSERT INTO gauge (id, name, display_name, location) VALUES (?, ?, ?, ?)'
        )->execute([
            self::GAUGE_NO_READINGS, 'CUSTOM_GAUGE_E', 'Custom Test Gauge (empty)', 'Cascadia',
        ]);

        foreach ([['flow', 850.0, 5.2], ['gauge', 4.2, 0.05], ['temperature', 52.0, null]] as [$dt, $v, $delta]) {
            $db->prepare(
                "INSERT INTO latest_gauge_observation
                    (gauge_id, data_type, value, observed_at, delta_per_hour)
                 VALUES (?, ?, ?, datetime('now', '-1 hour'), ?)"
            )->execute([self::GAUGE_WITH_READINGS, $dt, $v, $delta]);
        }

        // Three reaches: gauged-with-readings, gauged-no-readings, no-gauge.
        $reaches = [
            [self::REACH_GAUGED_WITH_READINGS, 'Custom Gauged Reach', self::GAUGE_WITH_READINGS],
            [self::REACH_GAUGED_NO_READINGS,   'Custom Empty Reach',  self::GAUGE_NO_READINGS],
            [self::REACH_NO_GAUGE,             'Custom No-Gauge Reach', null],
        ];
        foreach ($reaches as [$id, $name, $gauge_id]) {
            $db->prepare(
                'INSERT INTO reach (id, name, display_name, river, sort_name, gauge_id, no_show)
                 VALUES (?, ?, ?, ?, ?, ?, ?)'
            )->execute([$id, $name, $name, 'Custom River', strtolower($name), $gauge_id, 0]);
        }
    }

    public function testMissingIdsRedirectsToPicker(): void
    {
        $resp = $this->request('/custom.php');

        $this->assertSame(302, $resp['status']);
        $this->assertSame('/picker.php', $resp['headers']['location'] ?? '');
    }

    public function testInvalidIdsRedirectsToPicker(): void
    {
        // intval('abc') === 0; the > 0 filter drops them.
        $resp = $this->request('/custom.php', ['ids' => 'abc,-5,0,xyz']);

        $this->assertSame(302, $resp['status']);
        $this->assertSame('/picker.php', $resp['headers']['location'] ?? '');
    }

    public function testSingleReachRenders(): void
    {
        $resp = $this->request(
            '/custom.php',
            ['ids' => (string)self::REACH_GAUGED_WITH_READINGS],
        );

        $this->assertSame(200, $resp['status']);
        $this->assertResponseContains(
            $resp['body'],
            'Custom Gauged Reach',
            '1 reach',                    // "N reach[es]" count
            '<table class="levels">',
        );
        $this->assertNoBareInlineScript($resp['body']);
    }

    public function testMultiReachRendersInUrlOrder(): void
    {
        // Reverse-id order — if custom.php sorted by id ASC, this would
        // come back 6001, 6002, 6003. URL order means: 6003 first,
        // then 6001, then 6002.
        $resp = $this->request('/custom.php', [
            'ids' => implode(',', [self::REACH_NO_GAUGE, self::REACH_GAUGED_WITH_READINGS, self::REACH_GAUGED_NO_READINGS]),
        ]);

        $this->assertSame(200, $resp['status']);
        $this->assertResponseContains($resp['body'], '3 reaches');

        // Position of each reach in the body; URL order = ascending positions.
        $pos_no_gauge = strpos($resp['body'], 'Custom No-Gauge Reach');
        $pos_gauged = strpos($resp['body'], 'Custom Gauged Reach');
        $pos_empty = strpos($resp['body'], 'Custom Empty Reach');
        $this->assertNotFalse($pos_no_gauge);
        $this->assertNotFalse($pos_gauged);
        $this->assertNotFalse($pos_empty);
        $this->assertLessThan($pos_gauged, $pos_no_gauge,
            'URL order broken: no-gauge should appear before gauged');
        $this->assertLessThan($pos_empty, $pos_gauged,
            'URL order broken: gauged should appear before empty');
    }

    public function testGaugedReachShowsReadings(): void
    {
        $resp = $this->request(
            '/custom.php',
            ['ids' => (string)self::REACH_GAUGED_WITH_READINGS],
        );

        $this->assertSame(200, $resp['status']);
        // Flow renders as integer with thousands separator; 850 → "850"
        $this->assertStringContainsString('>850<', $resp['body']);
        // Gage at 2 decimals: 4.2 → "4.20"
        $this->assertStringContainsString('>4.20<', $resp['body']);
        // Temp at 0 decimals: 52 → "52"
        $this->assertStringContainsString('>52<', $resp['body']);
    }

    public function testNoGaugeReachRendersWithoutCrash(): void
    {
        $resp = $this->request(
            '/custom.php',
            ['ids' => (string)self::REACH_NO_GAUGE],
        );

        $this->assertSame(200, $resp['status']);
        $this->assertResponseContains(
            $resp['body'],
            'Custom No-Gauge Reach',
            '1 reach',
        );
    }
}
