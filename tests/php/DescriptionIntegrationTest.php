<?php

declare(strict_types=1);

require_once __DIR__ . '/IntegrationTestCase.php';

/**
 * Baseline integration tests for description.php (Phase 3.1 of
 * php_layer_split). Single-mode entry point — every request is detail
 * for a given ?id=<reach_id>. Covers:
 *
 *  - 400 on missing/invalid id (the entry-point guard at description.php:22).
 *  - Detail with a gauged reach: readings table is rendered (latest_gauge
 *    _observation rows present), data sources section appears, map div
 *    is emitted (Put-in/Take-out coords).
 *  - Detail without a gauge: readings/plots/sources sections all skipped.
 *  - Date-windowed call (?start, ?end): page still 200s; doesn't crash on
 *    valid YYYY-MM-DD inputs even with no observations in window.
 *
 * Seeding strategy matches ReachIntegrationTest: levels init-db handles
 * the schema + reference data; this class seeds two reaches + one gauge
 * + reach_state links + one observation row so the readings table has
 * something to render.
 */
final class DescriptionIntegrationTest extends IntegrationTestCase
{
    private const REACH_WITH_GAUGE_ID = 2001;
    private const REACH_NO_GAUGE_ID = 2002;
    private const GAUGE_ID = 6001;
    private const SOURCE_ID = 7001;
    private const REACH_WITH_GAUGE_NAME = 'Sandy Test Reach';
    private const REACH_NO_GAUGE_NAME = 'No Gauge Description Reach';

    protected static function seedDatabase(PDO $db): void
    {
        // Gauge with coords + a source linked to it (needed for the
        // "Data Sources" section render path).
        $db->prepare(
            'INSERT INTO gauge (id, name, display_name, latitude, longitude, usgs_id)
             VALUES (?, ?, ?, ?, ?, ?)'
        )->execute([
            self::GAUGE_ID, 'SANDY_TEST', 'Sandy Test Gauge',
            45.40, -122.30, '14142500',
        ]);

        // A USGS source row backing the gauge — exercises the
        // "Data Sources" rendering path. fetch_url_id stays NULL so
        // the source falls into the "—" rendering branch (simplest).
        $db->prepare(
            'INSERT INTO source (id, name, agency) VALUES (?, ?, ?)'
        )->execute([
            self::SOURCE_ID, 'sandy_usgs_test', 'USGS',
        ]);
        $db->prepare(
            'INSERT INTO gauge_source (gauge_id, source_id) VALUES (?, ?)'
        )->execute([self::GAUGE_ID, self::SOURCE_ID]);

        // Latest reading on the gauge — exercises the readings table
        // render at description.php:147-178.
        $db->prepare(
            'INSERT INTO latest_gauge_observation
                (gauge_id, data_type, value, observed_at, delta_per_hour)
             VALUES (?, ?, ?, ?, ?)'
        )->execute([
            self::GAUGE_ID, 'flow', 1234.5, '2026-05-12 10:00:00', 12.3,
        ]);

        // Reach 1: linked gauge + Put-in / Take-out coords.
        $db->prepare(
            'INSERT INTO reach
                (id, name, display_name, river, description, sort_name,
                 gauge_id, latitude_start, longitude_start, latitude_end,
                 longitude_end, no_show)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
        )->execute([
            self::REACH_WITH_GAUGE_ID,
            self::REACH_WITH_GAUGE_NAME,
            self::REACH_WITH_GAUGE_NAME,
            'Sandy',
            'A test reach for DescriptionIntegrationTest.',
            'sandy test reach',
            self::GAUGE_ID,
            45.38, -122.32,
            45.42, -122.28,
            0,
        ]);

        // Reach 2: no gauge, no coordinates — exercises the
        // no-readings / no-plots / no-map render path.
        $db->prepare(
            'INSERT INTO reach
                (id, name, display_name, river, description, sort_name, no_show)
             VALUES (?, ?, ?, ?, ?, ?, ?)'
        )->execute([
            self::REACH_NO_GAUGE_ID,
            self::REACH_NO_GAUGE_NAME,
            self::REACH_NO_GAUGE_NAME,
            'Nowhere',
            'A test reach with no gauge for DescriptionIntegrationTest.',
            'no gauge description reach',
            0,
        ]);

        // Link both to Oregon (needed for the State field render).
        $orId = (int)$db->query("SELECT id FROM state WHERE abbreviation = 'OR'")
            ->fetchColumn();
        $db->prepare('INSERT INTO reach_state (reach_id, state_id) VALUES (?, ?)')
            ->execute([self::REACH_WITH_GAUGE_ID, $orId]);
        $db->prepare('INSERT INTO reach_state (reach_id, state_id) VALUES (?, ?)')
            ->execute([self::REACH_NO_GAUGE_ID, $orId]);

        // Class III on the gauged reach so the Class field is non-empty.
        $db->prepare(
            'INSERT INTO reach_class (reach_id, name) VALUES (?, ?)'
        )->execute([self::REACH_WITH_GAUGE_ID, 'III']);
    }

    public function testMissingIdReturns400(): void
    {
        $resp = $this->request('/description.php');

        $this->assertSame(400, $resp['status']);
        $this->assertStringContainsString('Missing id parameter', $resp['body']);
    }

    public function testInvalidIdReturns400(): void
    {
        // filter_input(FILTER_VALIDATE_INT) returns false for non-int;
        // !$id catches that.
        $resp = $this->request('/description.php', ['id' => 'not-an-int']);

        $this->assertSame(400, $resp['status']);
    }

    public function testDetailModeRendersGaugedReach(): void
    {
        $resp = $this->request('/description.php', ['id' => self::REACH_WITH_GAUGE_ID]);

        $this->assertSame(200, $resp['status']);
        $this->assertResponseContains(
            $resp['body'],
            self::REACH_WITH_GAUGE_NAME,
            'Sandy',                         // River field
            'Flow',                          // readings table label
            '1,235 CFS',                     // formatted flow value (rounded)
            'Data Sources',                  // sources section header
            'Put-in',                        // coord field
            'Take-out',
            'Reach details',                 // footer button link
        );
        // No PHP-side CSP header (nginx owns it).
        $this->assertArrayNotHasKey('content-security-policy', $resp['headers']);
    }

    public function testDetailModeRendersNoGaugeReach(): void
    {
        $resp = $this->request('/description.php', ['id' => self::REACH_NO_GAUGE_ID]);

        $this->assertSame(200, $resp['status']);
        // description.php's $fields list doesn't include 'River', so the
        // string "Nowhere" (the reach.river column value) wouldn't appear.
        // Assert on what description.php actually renders: name in <h2>,
        // State field from reach_state, the description text, footer link.
        $this->assertResponseContains(
            $resp['body'],
            self::REACH_NO_GAUGE_NAME,
            'Oregon',
            'A test reach with no gauge',
            'Reach details',
        );
        // No gauge → no readings table, no data-sources section, no Put-in.
        $this->assertStringNotContainsString('Data Sources', $resp['body']);
        $this->assertStringNotContainsString('class="readings-table"', $resp['body']);
        $this->assertStringNotContainsString('Put-in', $resp['body']);
    }

    public function testDateWindowedCallStillRenders(): void
    {
        // Valid YYYY-MM-DD window — page renders even with no obs in window
        // (gp_render_plots handles empty data internally).
        $resp = $this->request('/description.php', [
            'id' => self::REACH_WITH_GAUGE_ID,
            'start' => '2026-04-01',
            'end' => '2026-05-01',
        ]);

        $this->assertSame(200, $resp['status']);
        $this->assertResponseContains(
            $resp['body'],
            self::REACH_WITH_GAUGE_NAME,
            '</html>',
        );
    }

    public function testInvalidDateIgnoredNotRejected(): void
    {
        // validate_date returns null for non-YYYY-MM-DD strings; entry
        // point doesn't reject — the date filter just becomes "no filter".
        $resp = $this->request('/description.php', [
            'id' => self::REACH_WITH_GAUGE_ID,
            'start' => 'garbage',
            'end' => '04/01/2026',  // wrong format
        ]);

        $this->assertSame(200, $resp['status']);
    }
}
