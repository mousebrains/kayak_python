<?php

declare(strict_types=1);

require_once __DIR__ . '/FunctionalTestCase.php';
require_once __DIR__ . '/Fixtures.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/db.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/header.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/footer.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/reach_detail.php';

/**
 * In-process functional coverage for reach_detail.php — drives
 * handle_reach_detail() directly so pcov counts the handler + all its
 * file-private render helpers (the subprocess integration tests can't).
 *
 * Seeds (once per class) a small reach corpus ordered by sort_name so
 * prev/next navigation is deterministic, plus the related rows (gauge,
 * state, reach_class ranges, guidebook, aw_id, geom, gradient_profile)
 * needed to drive each render branch:
 *
 *   "aaa first"  → first by sort_name (no Prev), no_show=1 (hidden list)
 *   "mmm full"   → fully-populated middle reach (Prev + Next, gauge w/
 *                  location + coords, class ranges, guidebook + AW row,
 *                  geom track, gradient profile, put-in/take-out, notes)
 *   "ppp ranges" → class rows WITHOUT low/high bounds (the "no ranges"
 *                  skip branch) + a gauge with no location/display_name
 *                  (name fallback) + reach display_name '' (name fallback)
 *   "zzz last"   → last by sort_name (no Next), bare reach (no gauge,
 *                  no coords, no geom, no aw_id, no guidebooks, no class)
 *
 * The "mmm full" reach is the centrepiece happy path; the others pin the
 * empty/edge branches and the navigation boundaries.
 */
final class ReachDetailFunctionalTest extends FunctionalTestCase
{
    private static int $firstId = 0;
    private static int $fullId = 0;
    private static int $rangesId = 0;
    private static int $lastId = 0;

    // Two non-overlapping significant bins → a renderable gradient chart.
    private const GRADIENT_PROFILE_JSON =
        '{"samples":[{"d_mi":0.0,"grad_ft_per_mi":50.0,"w_mi":6.0,"significant":true},'
        . '{"d_mi":6.0,"grad_ft_per_mi":50.0,"w_mi":6.0,"significant":true}]}';

    // < 2 samples → generate_gradient_profile_svg() returns '' (skip wrapper).
    private const GRADIENT_PROFILE_TINY =
        '{"samples":[{"d_mi":0.0,"grad_ft_per_mi":50.0,"w_mi":6.0,"significant":true}]}';

    protected static function seedDatabase(PDO $db): void
    {
        // --- Fully-populated gauge (location + coords) for the "full" reach.
        $gaugeFull = Fixtures::gauge($db, [
            'name' => 'FULL_TEST_GAUGE',
            'display_name' => 'Full Test Gauge',
            'location' => 'near Detroit',
            'latitude' => 44.74,
            'longitude' => -122.15,
        ]);

        // --- Bare gauge: no location, no display_name → name fallback path.
        $gaugeBare = Fixtures::gauge($db, [
            'name' => 'BARE_GAUGE',
            'display_name' => null,
            'location' => null,
            // No coords → gauge contributes no map point.
        ]);

        // --- "aaa first": hidden (no_show=1), first by sort_name.
        self::$firstId = Fixtures::reach($db, [
            'name' => 'Alpha Hidden Reach',
            'display_name' => 'Alpha Hidden Reach',
            'sort_name' => 'aaa first',
            'description' => 'First hidden reach.',
            'no_show' => 1,
        ]);

        // --- "mmm full": the centrepiece happy path.
        self::$fullId = Fixtures::reach($db, [
            'name' => 'Middle Full Reach',
            'display_name' => 'Middle Full Reach',
            'sort_name' => 'mmm full',
            'river' => 'Santiam',
            'description' => 'A fully populated reach. See https://example.com/run for details.',
            'difficulties' => 'Class IV at high water.',
            'notes' => "Scout the gorge.\nMore at https://example.org/notes",
            'gauge_id' => $gaugeFull,
            'latitude_start' => 44.70,
            'longitude_start' => -122.20,
            'latitude_end' => 44.78,
            'longitude_end' => -122.10,
            'basin' => 'Santiam Basin',
            'region' => 'Cascades',
            'season' => 'Spring',
            'nature' => 'Pool-drop',
            'watershed_type' => 'Snowmelt',
            'scenery' => 'Forested',
            'remoteness' => 'Roadside',
            'features' => 'Waterfall',
            'elevation' => 1200.0,
            'elevation_lost' => 600.0,
            'length' => 8.0,
            'gradient' => 75.0,
            'max_gradient' => 150.0,
            'optimal_flow' => 1500.0,
            'basin_area' => 250.0,
            'aw_id' => 42,
            'geom' => '-122.20 44.70,-122.15 44.74,-122.10 44.78',
            'gradient_profile' => self::GRADIENT_PROFILE_JSON,
        ]);

        // --- "ppp ranges": class rows WITHOUT bounds + bare gauge + no display_name.
        self::$rangesId = Fixtures::reach($db, [
            'name' => 'Papa Ranges Reach',
            'display_name' => '',               // empty → name fallback in handler
            'sort_name' => 'ppp ranges',
            'description' => 'Reach whose classes carry no flow bounds.',
            'gauge_id' => $gaugeBare,
            // tiny gradient profile → SVG '' branch (wrapper skipped)
            'gradient_profile' => self::GRADIENT_PROFILE_TINY,
        ]);

        // --- "zzz last": bare reach, last by sort_name (no Next).
        self::$lastId = Fixtures::reach($db, [
            'name' => 'Zulu Last Reach',
            'display_name' => 'Zulu Last Reach',
            'sort_name' => 'zzz last',
            'description' => '',                // empty → no " -- location" suffix
        ]);

        // Link the full reach to Oregon (Watershed "in Oregon").
        $orId = (int) $db->query("SELECT id FROM state WHERE abbreviation = 'OR'")->fetchColumn();
        Fixtures::reachClass($db, self::$fullId, [
            'name' => 'IV',
            'low' => 800.0,
            'low_data_type' => 'flow',
            'high' => 3000.0,
            'high_data_type' => 'flow',
        ]);
        $db->prepare('INSERT INTO reach_state (reach_id, state_id) VALUES (?, ?)')
            ->execute([self::$fullId, $orId]);

        // A second class on the full reach with a gauge-height range so the
        // class-ranges sub-table emits two rows incl. the data_type units.
        Fixtures::reachClass($db, self::$fullId, [
            'name' => 'V',
            'low' => 4.5,
            'low_data_type' => 'gauge',
            'high' => null,
            'high_data_type' => null,
        ]);

        // Guidebook + junction row for the full reach (title/subtitle/edition,
        // page + run, and a URL so the anchor-wrap branch fires).
        $gbId = (int) $db->query(
            "INSERT INTO guidebook (title, subtitle, edition, author, url, sort_order)
             VALUES ('Soggy Sneakers', 'A Paddlers Guide', '5th', 'WKCC', 'https://book.example/ss', 1)
             RETURNING id"
        )->fetchColumn();
        $db->prepare(
            'INSERT INTO reach_guidebook (reach_id, guidebook_id, page, run, url)
             VALUES (?, ?, ?, ?, ?)'
        )->execute([self::$fullId, $gbId, '212', 'Main', 'https://book.example/ss/main']);

        // The "ranges" reach gets two class rows that BOTH lack low/high — so
        // _render_reach_class_ranges sees classes !== [] but no ranges → skip.
        Fixtures::reachClass($db, self::$rangesId, ['name' => 'II']);
        Fixtures::reachClass($db, self::$rangesId, ['name' => 'III']);
    }

    public function testFullReachRendersEverything(): void
    {
        $html = $this->capture(
            fn() => handle_reach_detail($this->pdo(), self::$fullId, 0, '', '', '')
        );

        // Title + linked description anchor + h2 with description suffix.
        $this->assertStringContainsString('Middle Full Reach', $html);
        $this->assertStringContainsString('/description.php?h=' . pubhash_encode(self::$fullId), $html);
        $this->assertStringContainsString('A fully populated reach', $html);

        // Details-table fields.
        $this->assertStringContainsString('Santiam', $html);            // river
        $this->assertStringContainsString('IV, V', $html);              // Class list (both names)
        $this->assertStringContainsString('Santiam Basin in Oregon, Cascades', $html);
        $this->assertStringContainsString('250.0 sq mi', $html);        // Watershed Area
        $this->assertStringContainsString('8.0 mi, gradient 75 ft/mi, max 150 ft/mi', $html);
        $this->assertStringContainsString('1,200 ft to 600 ft, loss 600 ft', $html);
        $this->assertStringContainsString('low 800 CFS, high 3,000 CFS', $html);  // Flow line
        $this->assertStringContainsString('1,500 CFS', $html);          // Optimal Flow
        $this->assertStringContainsString('Waterfall', $html);          // Features
        // The internal-ID row was dropped from the details table.
        $this->assertStringNotContainsString('<th>ID</th>', $html);

        // Coordinate anchor fields (Put-in / Take-out) → google maps links.
        $this->assertStringContainsString('Put-in', $html);
        $this->assertStringContainsString('Take-out', $html);
        $this->assertStringContainsString('google.com/maps?q=44.700000,-122.200000', $html);

        // Autolinked description + notes (URLs become anchors).
        $this->assertStringContainsString('href="https://example.com/run"', $html);
        $this->assertStringContainsString('href="https://example.org/notes"', $html);

        // Class Ranges sub-table (two rows; data_type units appended).
        $this->assertStringContainsString('Class Ranges', $html);
        $this->assertStringContainsString('800.0 flow', $html);
        $this->assertStringContainsString('4.5 gauge', $html);

        // Guidebooks sub-table: AW row + the seeded guidebook anchor.
        $this->assertStringContainsString('Guidebooks', $html);
        $this->assertStringContainsString('American Whitewater', $html);
        $this->assertStringContainsString('river-detail/42/', $html);
        $this->assertStringContainsString('Soggy Sneakers — A Paddlers Guide (5th)', $html);
        $this->assertStringContainsString('p. 212, run Main', $html);
        $this->assertStringContainsString('href="https://book.example/ss/main"', $html);

        // Linked Gauge sub-table (display_name + Location row).
        $this->assertStringContainsString('Linked Gauge', $html);
        $this->assertStringContainsString('Full Test Gauge', $html);
        $this->assertStringContainsString('near Detroit', $html);

        // Map div + gradient profile + deferred leaflet scripts.
        $this->assertStringContainsString('id="reach-map"', $html);
        $this->assertStringContainsString('data-track=', $html);        // geom polyline present
        $this->assertStringContainsString('gradient-profile-container', $html);
        $this->assertStringContainsString('gradient-profile-chart', $html);
        $this->assertStringContainsString('/static/leaflet.js', $html);

        // Footer nav links.
        $this->assertStringContainsString('/data.php?h=' . pubhash_encode(self::$fullId), $html);
        $this->assertStringContainsString('Back to main page', $html);
    }

    public function testMiddleReachHasPrevAndNext(): void
    {
        // Among the visible (no_show=0) set sorted by sort_name —
        // 'mmm full' < 'ppp ranges' < 'zzz last' — the ranges reach sits in
        // the middle, so BOTH Prev and Next render as anchors (not the grey
        // spans). This pins the prev !== false and next !== false branches.
        $html = $this->capture(
            fn() => handle_reach_detail($this->pdo(), self::$rangesId, 0, '', '', '')
        );
        $this->assertStringContainsString('&laquo; Prev</a>', $html);
        $this->assertStringContainsString('Next &raquo;</a>', $html);
        // Reach N of M — three visible reaches (full, ranges, last).
        $this->assertStringContainsString('of 3</span>', $html);
        $this->assertStringContainsString('Reach 2 of 3', $html);
    }

    public function testFirstVisibleReachHasNoPrev(): void
    {
        // 'mmm full' sorts first among the visible reaches → Prev is the grey
        // span while Next is still an anchor (the prev === false branch).
        $html = $this->capture(
            fn() => handle_reach_detail($this->pdo(), self::$fullId, 0, '', '', '')
        );
        $this->assertStringContainsString('<span style="color:#999">&laquo; Prev</span>', $html);
        $this->assertStringContainsString('Next &raquo;</a>', $html);
        $this->assertStringContainsString('Reach 1 of 3', $html);
    }

    public function testLastReachHasNoNext(): void
    {
        // zzz sorts last among visible reaches → Next is the grey span.
        $html = $this->capture(
            fn() => handle_reach_detail($this->pdo(), self::$lastId, 0, '', '', '')
        );
        $this->assertStringContainsString('&laquo; Prev</a>', $html);                 // has prev
        $this->assertStringContainsString('<span style="color:#999">Next &raquo;</span>', $html);
        // Bare reach: no gauge, no coords, no geom → no map, no Linked Gauge,
        // no Put-in, no Guidebooks, no Class Ranges, no gradient profile,
        // no leaflet scripts.
        $this->assertStringNotContainsString('id="reach-map"', $html);
        $this->assertStringNotContainsString('Linked Gauge', $html);
        $this->assertStringNotContainsString('Put-in', $html);
        $this->assertStringNotContainsString('Guidebooks', $html);
        $this->assertStringNotContainsString('Class Ranges', $html);
        $this->assertStringNotContainsString('gradient-profile-container', $html);
        $this->assertStringNotContainsString('/static/leaflet.js', $html);
    }

    public function testFirstHiddenReachHasNoPrev(): void
    {
        // The only hidden (no_show=1) reach → with hidden=1 it's first AND
        // last in the hidden set: Prev and Next are both grey spans.
        $html = $this->capture(
            fn() => handle_reach_detail($this->pdo(), self::$firstId, 1, '', '', '')
        );
        $this->assertStringContainsString('<span style="color:#999">&laquo; Prev</span>', $html);
        $this->assertStringContainsString('<span style="color:#999">Next &raquo;</span>', $html);
        // hidden toggle link flips to "Show visible" + the hidden=0 target.
        $this->assertStringContainsString('Show visible', $html);
        $this->assertStringContainsString('hidden=0', $html);
        // hidden=1 carries through prev/next href suffix builder (no prev/next
        // here, but the hidden flag also adds the hidden form input).
        $this->assertStringContainsString('<input type="hidden" name="hidden" value="1">', $html);
    }

    public function testRangesReachSkipsEmptyClassRangesAndUsesNameFallback(): void
    {
        // display_name '' on the reach + a gauge with null display_name and
        // null location → both fall back to the bare `name`.
        $html = $this->capture(
            fn() => handle_reach_detail($this->pdo(), self::$rangesId, 0, '', '', '')
        );
        // Reach name fallback (display_name was '').
        $this->assertStringContainsString('Papa Ranges Reach', $html);
        // Gauge name fallback in the Linked Gauge row (no display_name).
        $this->assertStringContainsString('Linked Gauge', $html);
        $this->assertStringContainsString('BARE_GAUGE', $html);
        // Gauge has no location → no Location row.
        $this->assertStringNotContainsString('<tr><td>Location</td>', $html);
        // Two classes exist but neither has low/high → Class field lists the
        // names, but the Class Ranges sub-table is skipped.
        $this->assertStringContainsString('II, III', $html);
        $this->assertStringNotContainsString('Class Ranges', $html);
        // Tiny (<2 sample) gradient profile → wrapper skipped.
        $this->assertStringNotContainsString('gradient-profile-container', $html);
        // Bare gauge has no coords, reach has no coords/geom → no map.
        $this->assertStringNotContainsString('id="reach-map"', $html);
    }

    public function testNavBarEchoesSearchQueryAndSelectedState(): void
    {
        // Non-empty q + st flow through to the embedded search form: q is
        // echoed in the text input value, and st marks the matching <option>.
        $html = $this->capture(
            fn() => handle_reach_detail($this->pdo(), self::$fullId, 0, 'rapids', 'OR', '')
        );
        $this->assertStringContainsString('value="rapids"', $html);
        $this->assertStringContainsString('<option value="OR" selected>OR</option>', $html);
        // hidden=0 → toggle link offers "Show hidden".
        $this->assertStringContainsString('Show hidden', $html);
    }

    public function testCompactCssIsThreadedIntoHead(): void
    {
        // The $compact_css arg is appended into extra_head verbatim.
        $marker = '<style>/*REACH_DETAIL_COMPACT_MARKER*/</style>';
        $html = $this->capture(
            fn() => handle_reach_detail($this->pdo(), self::$lastId, 0, '', '', $marker)
        );
        $this->assertStringContainsString($marker, $html);
        // gauge_map head link (gm_head_links) also lands in <head>.
        $this->assertStringContainsString('/static/leaflet.css', $html);
    }

    public function testUnknownReachIs404(): void
    {
        $e = $this->captureExit(
            fn() => handle_reach_detail($this->pdo(), 999999, 0, '', '', '')
        );
        $this->assertSame(404, $e->statusCode);
        $this->assertSame('Reach not found', $e->getMessage());
    }
}
