<?php

declare(strict_types=1);

require_once __DIR__ . '/IntegrationTestCase.php';

/**
 * Baseline integration tests for gauge.php (Phase 5.G.1 of
 * php_layer_split). Multi-mode entry point like reach.php:
 *   /gauge.php                  → default to first gauge (detail mode)
 *   /gauge.php?id=N             → detail mode
 *   /gauge.php?q=<term>         → search; single match auto-redirects
 *
 * Covers:
 *  - Default fallback: no params → first-gauge detail
 *  - Invalid id → 404
 *  - Search no match → "No gauges matching" empty state
 *  - Search single match → 302 to detail
 *  - Search multi match → results table
 *  - Detail with readings → readings table, no stale banner, map,
 *    associated sources, associated reaches
 *  - Detail without readings → "No cached observations" banner; no
 *    readings table
 *
 * Seed: two gauges (id=4001 with full data + reach + readings + source,
 * id=4002 minimal/no-readings). One associated reach on 4001 so the
 * "Associated Reaches" section renders and the per-reach status
 * classification path is exercised.
 */
final class GaugeIntegrationTest extends IntegrationTestCase
{
    private const GAUGE_WITH_DATA_ID = 4001;
    private const GAUGE_NO_DATA_ID = 4002;
    private const SOURCE_ID = 7501;
    private const REACH_ID = 4501;
    private const GAUGE_WITH_DATA_NAME = 'Clackamas Test Gauge';
    private const GAUGE_NO_DATA_NAME = 'Empty Test Gauge';

    protected static function seedDatabase(PDO $db): void
    {
        // Gauge 1: full data — coords + USGS id + a source + a reading.
        $db->prepare(
            'INSERT INTO gauge
                (id, name, display_name, location, latitude, longitude,
                 usgs_id, station_id)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
        )->execute([
            self::GAUGE_WITH_DATA_ID,
            'CLACKAMAS_TEST',
            self::GAUGE_WITH_DATA_NAME,
            'Estacada, OR',
            45.30, -122.35,
            '14210000',
            'CLAC',
        ]);

        // Gauge 2: minimal — no readings, no associated reach.
        // Used to assert the "No cached observations for this gauge"
        // banner path renders without crashing.
        $db->prepare(
            'INSERT INTO gauge (id, name, display_name) VALUES (?, ?, ?)'
        )->execute([
            self::GAUGE_NO_DATA_ID,
            'EMPTY_GAUGE_TEST',
            self::GAUGE_NO_DATA_NAME,
        ]);

        // Source backing gauge 1 + one observation so the readings table
        // renders.
        $db->prepare(
            'INSERT INTO source (id, name, agency) VALUES (?, ?, ?)'
        )->execute([self::SOURCE_ID, 'clackamas_test_source', 'USGS']);
        $db->prepare(
            'INSERT INTO gauge_source (gauge_id, source_id) VALUES (?, ?)'
        )->execute([self::GAUGE_WITH_DATA_ID, self::SOURCE_ID]);

        // Use a recent timestamp so the stale-banner code path (>7 days)
        // does NOT trigger on the gauged test. Tests run with `php -S`;
        // observed_at is compared against time() inside the entry-point.
        $recent = date('Y-m-d H:i:s', time() - 3600);
        $db->prepare(
            'INSERT INTO latest_gauge_observation
                (gauge_id, data_type, value, observed_at, delta_per_hour)
             VALUES (?, ?, ?, ?, ?)'
        )->execute([
            self::GAUGE_WITH_DATA_ID, 'flow', 875.0, $recent, 5.2,
        ]);

        // One associated reach so the Associated Reaches section + the
        // per-reach status classification path runs.
        $db->prepare(
            'INSERT INTO reach
                (id, name, display_name, river, sort_name, gauge_id, no_show)
             VALUES (?, ?, ?, ?, ?, ?, ?)'
        )->execute([
            self::REACH_ID,
            'Clackamas Above Estacada',
            'Clackamas Above Estacada',
            'Clackamas',
            'clackamas above estacada',
            self::GAUGE_WITH_DATA_ID,
            0,
        ]);
        $db->prepare(
            'INSERT INTO reach_class (reach_id, name, low, high, low_data_type, high_data_type)
             VALUES (?, ?, ?, ?, ?, ?)'
        )->execute([
            self::REACH_ID, 'III', 500.0, 2000.0, 'flow', 'flow',
        ]);
    }

    public function testDefaultPathFallsThroughToFirstGauge(): void
    {
        // No ?id and no ?q — falls through to "first gauge by id ASC".
        $resp = $this->request('/gauge.php');

        $this->assertSame(200, $resp['status']);
        $this->assertResponseContains(
            $resp['body'],
            'of 2',                          // "Gauge N of 2" nav
            'Back to main page',
        );
        $this->assertNoBareInlineScript($resp['body']);
    }

    public function testInvalidIdReturns404(): void
    {
        $resp = $this->request('/gauge.php', ['id' => 999999]);

        $this->assertSame(404, $resp['status']);
        $this->assertStringContainsString('Gauge not found', $resp['body']);
    }

    public function testSearchModeNoMatch(): void
    {
        $resp = $this->request('/gauge.php', ['q' => 'no-such-gauge-zzz']);

        $this->assertSame(200, $resp['status']);
        $this->assertStringContainsString('No gauges matching', $resp['body']);
        $this->assertNoBareInlineScript($resp['body']);
    }

    public function testSearchModeSingleMatchRedirects(): void
    {
        // Unique substring of one gauge name → single match → 302 redirect.
        $resp = $this->request('/gauge.php', ['q' => 'CLACKAMAS_TEST']);

        $this->assertSame(302, $resp['status']);
        $this->assertSame(
            '/gauge.php?id=' . self::GAUGE_WITH_DATA_ID,
            $resp['headers']['location'] ?? '',
        );
    }

    public function testSearchModeMultiMatchRendersTable(): void
    {
        // 'TEST' is in both seeded gauges' canonical name → multi-match.
        // Search SQL (gauge.php:27-34) selects/renders the `name` column,
        // NOT `display_name`, so assert on the canonical names here
        // (detail-mode tests below assert on display_name instead).
        $resp = $this->request('/gauge.php', ['q' => 'TEST']);

        $this->assertSame(200, $resp['status']);
        $this->assertResponseContains(
            $resp['body'],
            'gauges matching',
            'CLACKAMAS_TEST',
            'EMPTY_GAUGE_TEST',
            '</html>',
        );
        $this->assertNoBareInlineScript($resp['body']);
    }

    public function testDetailModeWithReadings(): void
    {
        $resp = $this->request('/gauge.php', ['id' => self::GAUGE_WITH_DATA_ID]);

        $this->assertSame(200, $resp['status']);
        $this->assertResponseContains(
            $resp['body'],
            self::GAUGE_WITH_DATA_NAME,
            'Estacada, OR',                  // Location field
            '14210000',                      // USGS ID field
            'Flow',                          // readings table label
            '875 CFS',                       // formatted flow value
            'Associated Sources',            // sources section
            'Associated Reaches',            // reaches section
            'Clackamas Above Estacada',      // associated reach name
        );
        // Readings within 1 hour of now → no stale banner.
        $this->assertStringNotContainsString(
            'No cached observations',
            $resp['body'],
        );
        $this->assertStringNotContainsString(
            'days ago',
            $resp['body'],
        );
        $this->assertNoBareInlineScript($resp['body']);
    }

    public function testDetailModeNoReadingsRendersStaleBanner(): void
    {
        $resp = $this->request('/gauge.php', ['id' => self::GAUGE_NO_DATA_ID]);

        $this->assertSame(200, $resp['status']);
        $this->assertResponseContains(
            $resp['body'],
            self::GAUGE_NO_DATA_NAME,
            'No cached observations',        // empty-readings banner
            'No associated sources',         // no-sources message
            'No associated reaches',         // no-reaches message
        );
        // No readings → no readings table.
        $this->assertStringNotContainsString(
            'class="readings-table"',
            $resp['body'],
        );
        $this->assertNoBareInlineScript($resp['body']);
    }
}
