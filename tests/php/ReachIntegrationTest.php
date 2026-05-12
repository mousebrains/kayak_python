<?php

declare(strict_types=1);

require_once __DIR__ . '/IntegrationTestCase.php';

/**
 * Baseline integration tests for reach.php (Phase 2.1 of php_layer_split).
 *
 * Covers the four cases the plan calls out before the Tier 2 extraction
 * work starts:
 *  - Search mode: ?q=<term> renders the search-results template.
 *  - List mode: ? (no params) renders the empty-state or first-reach
 *    fallback (this codebase falls through to "default to first reach";
 *    with one seeded reach, that's a detail render).
 *  - Detail mode: ?id=<class-2-with-gauge> renders the full detail view
 *    including the Linked Gauge section and the map div.
 *  - Detail edge: ?id=<no-gauge-reach> renders the detail view but with
 *    no Linked Gauge section and no map div (Put-in/Take-out absent).
 *
 * Seeding strategy: `levels init-db` provides the schema + reference
 * data (states, sources). Two reach rows + one gauge + the necessary
 * reach_state link are inserted in seedDatabase(). All seeded data is
 * read-only across tests; PHPUnit reuses the same DB per class.
 *
 * The substring assertions are intentionally narrow — they pin the
 * mode-discriminating bits of the template, not the entire body
 * (which is in flux during the split work).
 */
final class ReachIntegrationTest extends IntegrationTestCase
{
    private const REACH_WITH_GAUGE_ID = 1001;
    private const REACH_NO_GAUGE_ID = 1002;
    private const GAUGE_ID = 5001;
    private const REACH_WITH_GAUGE_NAME = 'Willamette Test Reach';
    private const REACH_NO_GAUGE_NAME = 'No Gauge Test Reach';

    protected static function seedDatabase(PDO $db): void
    {
        // Reach 1: full detail with linked gauge + state + class.
        $db->prepare(
            'INSERT INTO gauge (id, name, display_name, latitude, longitude)
             VALUES (?, ?, ?, ?, ?)'
        )->execute([
            self::GAUGE_ID, 'WILLAMETTE_TEST', 'Willamette Test Gauge', 44.55, -123.25,
        ]);

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
            'Willamette',
            'A test reach used by ReachIntegrationTest.',
            'willamette test reach',
            self::GAUGE_ID,
            44.50, -123.30,
            44.60, -123.20,
            0,
        ]);

        // Reach 2: no gauge, no coordinates (no-map edge case).
        $db->prepare(
            'INSERT INTO reach
                (id, name, display_name, river, description, sort_name, no_show)
             VALUES (?, ?, ?, ?, ?, ?, ?)'
        )->execute([
            self::REACH_NO_GAUGE_ID,
            self::REACH_NO_GAUGE_NAME,
            self::REACH_NO_GAUGE_NAME,
            'Test River',
            'A test reach with no gauge.',
            'no gauge test reach',
            0,
        ]);

        // Link both reaches to Oregon via reach_state (so state-filter mode
        // queries match). Oregon's row id comes from init-db's _seed_states.
        $orId = (int)$db->query("SELECT id FROM state WHERE abbreviation = 'OR'")
            ->fetchColumn();
        $db->prepare('INSERT INTO reach_state (reach_id, state_id) VALUES (?, ?)')
            ->execute([self::REACH_WITH_GAUGE_ID, $orId]);
        $db->prepare('INSERT INTO reach_state (reach_id, state_id) VALUES (?, ?)')
            ->execute([self::REACH_NO_GAUGE_ID, $orId]);

        // One reach_class row on the gauged reach (so the detail page's
        // "Class" field is populated).
        $db->prepare(
            'INSERT INTO reach_class (reach_id, name) VALUES (?, ?)'
        )->execute([self::REACH_WITH_GAUGE_ID, 'III']);
    }

    public function testSearchModeRendersResults(): void
    {
        // ?q=Willamette should match the seeded reach by river name AND
        // hit the auto-redirect (single result) — assert the 302.
        $resp = $this->request('/reach.php', ['q' => 'Willamette']);

        $this->assertSame(
            302,
            $resp['status'],
            'single search-result match should 302 to /reach.php?id=<single>',
        );
        $this->assertSame(
            '/reach.php?id=' . self::REACH_WITH_GAUGE_ID,
            $resp['headers']['location'] ?? '',
        );
    }

    public function testSearchModeMultiResultRendersTable(): void
    {
        // 'Test' matches both seeded reaches. _search_reaches_query LIKEs
        // display_name OR name OR river (NOT description): the gauged
        // reach matches via display_name/name ("Willamette Test Reach")
        // and the no-gauge reach matches via display_name/name + river
        // ("No Gauge Test Reach" / "Test River").
        $resp = $this->request('/reach.php', ['q' => 'Test']);

        $this->assertSame(200, $resp['status']);
        $this->assertResponseContains(
            $resp['body'],
            'reaches matching',
            self::REACH_WITH_GAUGE_NAME,
            self::REACH_NO_GAUGE_NAME,
            '</html>',
        );
        $this->assertNoBareInlineScript($resp['body']);
    }

    public function testStateFilterListMode(): void
    {
        // ?st=OR — both seeded reaches are linked to Oregon, neither is
        // hidden. Renders as the state-filter list (count > 1, no
        // auto-redirect).
        $resp = $this->request('/reach.php', ['st' => 'OR']);

        $this->assertSame(200, $resp['status']);
        $this->assertResponseContains(
            $resp['body'],
            'reaches matching',
            self::REACH_WITH_GAUGE_NAME,
            self::REACH_NO_GAUGE_NAME,
            '</html>',
        );
        $this->assertNoBareInlineScript($resp['body']);
    }

    public function testDefaultPathFallsThroughToFirstReach(): void
    {
        // No ?id and no ?q — falls through to "default to first reach"
        // (lines 315-328 of reach.php). With reaches seeded, this is a
        // detail render of whichever reach sorts first.
        $resp = $this->request('/reach.php');

        $this->assertSame(200, $resp['status']);
        // Whatever reach got rendered, the detail-mode markers are present.
        $this->assertResponseContains(
            $resp['body'],
            '<h2>',           // detail-mode title heading
            'Reach',          // 'Reach N of M' nav line
            'of 2',           // exactly 2 seeded reaches
            'Back to main page',
        );
        $this->assertNoBareInlineScript($resp['body']);
    }

    public function testDetailModeRendersGaugedReach(): void
    {
        $resp = $this->request('/reach.php', ['id' => self::REACH_WITH_GAUGE_ID]);

        $this->assertSame(200, $resp['status']);
        $this->assertResponseContains(
            $resp['body'],
            self::REACH_WITH_GAUGE_NAME,
            'Willamette',                  // river field
            'Linked Gauge',                // section appears only when reach.gauge_id is set
            'Willamette Test Gauge',
            'id="reach-map"',              // the actual map div (not the CSS rule)
            'Put-in',                      // coordinates section
            'Take-out',
            'III',                         // reach_class row
        );
        // CSP header must not be set by PHP (nginx owns it in prod).
        $this->assertArrayNotHasKey('content-security-policy', $resp['headers']);
        $this->assertNoBareInlineScript($resp['body']);
    }

    public function testDetailModeRendersNoGaugeReach(): void
    {
        $resp = $this->request('/reach.php', ['id' => self::REACH_NO_GAUGE_ID]);

        $this->assertSame(200, $resp['status']);
        $this->assertResponseContains(
            $resp['body'],
            self::REACH_NO_GAUGE_NAME,
            'Test River',
            '</html>',
        );
        // No gauge → no "Linked Gauge" section, no map div, no Put-in row.
        // Use the div's id="…" attribute marker rather than the bare token —
        // the compact_css block at the top of every reach.php response
        // includes the CSS rules for #reach-map/#search-map regardless of
        // whether the actual <div id="reach-map"> was emitted.
        $this->assertStringNotContainsString('Linked Gauge', $resp['body']);
        $this->assertStringNotContainsString('id="reach-map"', $resp['body']);
        $this->assertStringNotContainsString('Put-in', $resp['body']);
        $this->assertNoBareInlineScript($resp['body']);
    }
}
