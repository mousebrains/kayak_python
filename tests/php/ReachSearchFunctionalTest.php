<?php

declare(strict_types=1);

require_once __DIR__ . '/FunctionalTestCase.php';
require_once __DIR__ . '/Fixtures.php';
require_once __DIR__ . '/../../php/includes/db.php';
require_once __DIR__ . '/../../php/includes/header.php';
require_once __DIR__ . '/../../php/includes/footer.php';
require_once __DIR__ . '/../../php/includes/gauge_map.php';
require_once __DIR__ . '/../../php/includes/reach_search.php';

/**
 * In-process functional coverage for reach_search.php
 * (handle_search_mode + the query/aggregation/render helpers).
 *
 * handle_search_mode is `: never` — it always ends via http_terminate()
 * (302 on a single match, 200 after rendering). Both throw
 * HttpExitException under KAYAK_TEST, so {@see captureSearch} captures
 * the buffered HTML *and* the thrown status code together.
 *
 * Covers all three query variants (q+st, q only, st only), the
 * single-match redirect, the no-match empty state, the results table
 * with/without gauge readings, the class/guidebook rollup (Soggy
 * Sneakers editions, non-SS abbrevs, AW-from-aw_id), and the map
 * payload (reach coords + downsampled geom track + gauges with coords),
 * plus the no-coords → no-map branch.
 */
final class ReachSearchFunctionalTest extends FunctionalTestCase
{
    private static int $reachAlpha = 0;   // "Alpha River", OR, gauge w/ flow+gauge, coords + geom
    private static int $reachAlpine = 0;  // "Alpine Creek", OR, inflow-only gauge, coords, no geom
    private static int $reachBeta = 0;    // "Beta Brook", WA, no gauge, no coords
    private static int $reachBeta2 = 0;   // "Beta Falls", WA, no gauge, no coords (pairs w/ Beta for no-map)
    private static int $reachSolo = 0;    // "ZZZ Unique Solo", unique term for single-match redirect
    private static int $gaugeFlow = 0;
    private static int $gaugeInflow = 0;
    private static int $orStateId = 0;

    /**
     * Run a `: never` search handler, returning [rendered_html, status_code].
     * captureExit() discards the buffer; the multi/zero-match render path
     * needs the HTML, so capture it before re-reading the thrown status.
     *
     * @return array{0: string, 1: int}
     */
    private function captureSearch(callable $fn): array
    {
        ob_start();
        try {
            $fn();
        } catch (HttpExitException $e) {
            return [(string) ob_get_clean(), $e->statusCode];
        } catch (\Throwable $e) {
            ob_end_clean();
            throw $e;
        }
        ob_end_clean();
        $this->fail('Expected HttpExitException from the never-returning handler.');
    }

    protected static function seedDatabase(PDO $db): void
    {
        self::$orStateId = self::stateId($db, 'OR');
        $waStateId = self::stateId($db, 'WA');

        // --- gauge with flow + gauge readings, coords ---
        self::$gaugeFlow = Fixtures::gauge($db, [
            'name' => 'GAUGE_FLOW', 'latitude' => 45.3, 'longitude' => -122.1,
        ]);
        $srcFlow = Fixtures::source($db);
        Fixtures::linkGaugeSource($db, self::$gaugeFlow, $srcFlow);
        Fixtures::latestObservation($db, $srcFlow, ['data_type' => 'flow', 'value' => 1234.0]);
        Fixtures::latestObservation($db, $srcFlow, ['data_type' => 'gauge', 'value' => 5.5]);

        // --- gauge with inflow only (no flow), no coords ---
        self::$gaugeInflow = Fixtures::gauge($db, ['name' => 'GAUGE_INFLOW']);
        $srcInflow = Fixtures::source($db);
        Fixtures::linkGaugeSource($db, self::$gaugeInflow, $srcInflow);
        Fixtures::latestObservation($db, $srcInflow, ['data_type' => 'inflow', 'value' => 777.0]);

        // --- guidebooks: a Soggy Sneakers edition (id 9 ⇒ SS1) + a non-SS ---
        Fixtures::insertReturning($db, 'guidebook', ['id' => 9, 'title' => 'Soggy Sneakers']);
        Fixtures::insertReturning($db, 'guidebook', ['id' => 7, 'title' => 'Paddling Oregon']);

        // --- Alpha River: gauged (flow), coords + a multi-point geom track ---
        // geom is "lon lat,lon lat,..." (the renderer flips to [lat,lon]).
        $geomPts = [];
        for ($i = 0; $i < 130; $i++) {  // > 100 → exercises the downsample.
            $lon = -122.1 + $i * 0.001;
            $lat = 45.3 + $i * 0.001;
            $geomPts[] = "$lon $lat";
        }
        self::$reachAlpha = Fixtures::reach($db, [
            'name' => 'Alpha River', 'display_name' => 'Alpha River', 'river' => 'Alpha',
            'description' => 'A scenic run', 'sort_name' => 'alpha river',
            'gauge_id' => self::$gaugeFlow, 'no_show' => 0,
            'latitude' => 45.3, 'longitude' => -122.1,
            'latitude_start' => 45.30, 'longitude_start' => -122.10,
            'latitude_end' => 45.43, 'longitude_end' => -121.97,
            'aw_id' => 4321, 'geom' => implode(',', $geomPts),
        ]);
        Fixtures::reachClass($db, self::$reachAlpha, ['name' => 'III']);
        Fixtures::linkReachState($db, self::$reachAlpha, self::$orStateId);
        Fixtures::linkReachGuidebook($db, self::$reachAlpha, 9);  // SS1
        Fixtures::linkReachGuidebook($db, self::$reachAlpha, 7);  // PO

        // --- Alpine Creek: inflow-only gauge, coords (start only), no geom ---
        self::$reachAlpine = Fixtures::reach($db, [
            'name' => 'Alpine Creek', 'display_name' => 'Alpine Creek', 'river' => 'Alpine',
            'sort_name' => 'alpine creek', 'gauge_id' => self::$gaugeInflow, 'no_show' => 0,
            'latitude_start' => 44.1, 'longitude_start' => -121.5,
        ]);
        Fixtures::linkReachState($db, self::$reachAlpine, self::$orStateId);

        // --- Beta Brook + Beta Falls: no gauge, no coords, WA. The shared
        //     "Beta" token gives a coords-free multi-match → no-map branch.
        self::$reachBeta = Fixtures::reach($db, [
            'name' => 'Beta Brook', 'display_name' => 'Beta Brook', 'river' => 'Beta',
            'sort_name' => 'beta brook', 'no_show' => 0,
        ]);
        Fixtures::linkReachState($db, self::$reachBeta, $waStateId);
        self::$reachBeta2 = Fixtures::reach($db, [
            'name' => 'Beta Falls', 'display_name' => 'Beta Falls', 'river' => 'Beta',
            'sort_name' => 'beta falls', 'no_show' => 0,
        ]);
        Fixtures::linkReachState($db, self::$reachBeta2, $waStateId);

        // --- Solo: a uniquely-named reach for the single-match redirect ---
        self::$reachSolo = Fixtures::reach($db, [
            'name' => 'ZZZ Unique Solo', 'display_name' => 'ZZZ Unique Solo',
            'sort_name' => 'zzz unique solo', 'no_show' => 0,
        ]);

        // --- Hidden Gamma: no_show=1 — only visible when hidden=1 is passed,
        //     so the no_show bind param is exercised in both states.
        $hidden = Fixtures::reach($db, [
            'name' => 'Hidden Gamma', 'display_name' => 'Hidden Gamma',
            'sort_name' => 'hidden gamma', 'no_show' => 1,
        ]);
        Fixtures::linkReachState($db, $hidden, self::$orStateId);

        // --- Delta Upper + Delta Lower: coords but NO gauge. A "Delta"
        //     multi-match renders a map (coords present) yet has an empty
        //     gauge_ids list → _collect_search_map_gauges early-returns [].
        Fixtures::reach($db, [
            'name' => 'Delta Upper', 'display_name' => 'Delta Upper', 'river' => 'Delta',
            'sort_name' => 'delta upper', 'no_show' => 0,
            'latitude' => 43.5, 'longitude' => -123.2,
        ]);
        Fixtures::reach($db, [
            'name' => 'Delta Lower', 'display_name' => 'Delta Lower', 'river' => 'Delta',
            'sort_name' => 'delta lower', 'no_show' => 0,
            'latitude' => 43.6, 'longitude' => -123.1,
        ]);
    }

    /** Look up a seeded state id by two-letter abbreviation. */
    private static function stateId(PDO $db, string $abbrev): int
    {
        $stmt = $db->prepare('SELECT id FROM state WHERE abbreviation = ?');
        $stmt->execute([$abbrev]);
        return (int) $stmt->fetchColumn();
    }

    public function testSingleMatchRedirects(): void
    {
        // "Solo" matches exactly one reach → 302 to its detail page.
        [$html, $status] = $this->captureSearch(
            fn() => handle_search_mode($this->pdo(), 'Unique Solo', '', 0, '')
        );
        $this->assertSame(302, $status);
        $this->assertSame('', $html, 'Redirect path must not render a body');
    }

    public function testMultiMatchQueryOnlyRendersTableAndMap(): void
    {
        // "Alp" matches Alpha River + Alpine Creek (q-only variant).
        [$html, $status] = $this->captureSearch(
            fn() => handle_search_mode($this->pdo(), 'Alp', '', 0, '')
        );
        $this->assertSame(200, $status);
        $this->assertStringContainsString('<h2>Reach Search</h2>', $html);
        $this->assertStringContainsString('2 reaches matching', $html);
        $this->assertStringContainsString('&ldquo;Alp&rdquo;', $html);
        $this->assertStringContainsString('Alpha River', $html);
        $this->assertStringContainsString('Alpine Creek', $html);

        // Flow reading on Alpha (flow wins): "1,234 cfs / 5.50 ft".
        $this->assertStringContainsString('1,234 cfs', $html);
        $this->assertStringContainsString('5.50 ft', $html);
        // Inflow fallback on Alpine (no flow row): "777 cfs".
        $this->assertStringContainsString('777 cfs', $html);

        // Guides rollup: SS1 + PO on Alpha, plus AW from its aw_id.
        $this->assertStringContainsString('SS1', $html);
        $this->assertStringContainsString('PO', $html);
        $this->assertStringContainsString('AW', $html);
        // Class column.
        $this->assertStringContainsString('III', $html);

        // ID column is now the base-62 handle, hyperlinked to the reach page
        // (no bare decimal id on the public search results).
        $alphaHandle = pubhash_encode(self::$reachAlpha);
        $this->assertStringContainsString(
            '<td><a href="/reach.php?h=' . $alphaHandle . '">' . $alphaHandle . '</a></td>',
            $html,
        );
        $this->assertStringNotContainsString('<td>' . self::$reachAlpha . '</td>', $html);

        // Map: both reaches have coords → search-map div + deferred scripts.
        $this->assertStringContainsString('id="search-map"', $html);
        $this->assertStringContainsString('search-map.js', $html);
        // Map swatch in the table.
        $this->assertStringContainsString('Map marker color', $html);
        // Gauge with coords appears in the map's gauges payload.
        $this->assertStringContainsString('GAUGE_FLOW', $html);
    }

    public function testQueryAndStateVariant(): void
    {
        // q + st: "Alp" restricted to OR → both Alp reaches (Beta is WA).
        [$html, $status] = $this->captureSearch(
            fn() => handle_search_mode($this->pdo(), 'Alp', 'OR', 0, '')
        );
        $this->assertSame(200, $status);
        $this->assertStringContainsString('Alpha River', $html);
        $this->assertStringContainsString('Alpine Creek', $html);
        $this->assertStringNotContainsString('Beta Brook', $html);
    }

    public function testStateOnlyVariant(): void
    {
        // st only (no q): every visible OR reach. Label is the state abbrev.
        [$html, $status] = $this->captureSearch(
            fn() => handle_search_mode($this->pdo(), '', 'OR', 0, '')
        );
        $this->assertSame(200, $status);
        $this->assertStringContainsString('reaches matching OR', $html);
        $this->assertStringContainsString('Alpha River', $html);
        $this->assertStringContainsString('Alpine Creek', $html);
        $this->assertStringNotContainsString('Beta Brook', $html);
    }

    public function testNoMatchEmptyState(): void
    {
        [$html, $status] = $this->captureSearch(
            fn() => handle_search_mode($this->pdo(), 'nonexistent-zzz-term', '', 0, '')
        );
        $this->assertSame(200, $status);
        $this->assertStringContainsString('No reaches matching', $html);
        $this->assertStringContainsString('&ldquo;nonexistent-zzz-term&rdquo;', $html);
        // No results → no map.
        $this->assertStringNotContainsString('id="search-map"', $html);
    }

    public function testCoordsFreeMultiMatchRendersNoMap(): void
    {
        // "Beta" matches Beta Brook + Beta Falls — a multi-match where every
        // reach lacks coords, so the table renders but _render_search_map
        // returns [false, ''] (no <div id="search-map">, no map scripts).
        [$html, $status] = $this->captureSearch(
            fn() => handle_search_mode($this->pdo(), 'Beta', '', 0, '')
        );
        $this->assertSame(200, $status);
        $this->assertStringContainsString('2 reaches matching', $html);
        $this->assertStringContainsString('Beta Brook', $html);
        $this->assertStringContainsString('Beta Falls', $html);
        $this->assertStringNotContainsString('id="search-map"', $html);
        $this->assertStringNotContainsString('search-map.js', $html);
        // No gauge → empty reading column (no "cfs"/"ft").
        $this->assertStringNotContainsString('cfs', $html);
    }

    public function testCoordsWithoutGaugesStillRendersMap(): void
    {
        // "Delta" matches two reaches with coords but no gauges → the map div
        // renders (coords present) while _collect_search_map_gauges gets an
        // empty gauge_ids list and early-returns [].
        [$html, $status] = $this->captureSearch(
            fn() => handle_search_mode($this->pdo(), 'Delta', '', 0, '')
        );
        $this->assertSame(200, $status);
        $this->assertStringContainsString('Delta Upper', $html);
        $this->assertStringContainsString('Delta Lower', $html);
        // Map renders from reach coords...
        $this->assertStringContainsString('id="search-map"', $html);
        // ...but with an empty gauges payload.
        $this->assertStringContainsString('data-gauges="[]"', $html);
    }

    public function testHiddenFlagSurfacesNoShowReaches(): void
    {
        // The seeded no_show=1 "Hidden Gamma" is excluded by default (hidden=0)
        // but included with hidden=1 — drives both bind values of no_show.
        [$htmlDefault] = $this->captureSearch(
            fn() => handle_search_mode($this->pdo(), 'Gamma', '', 0, '')
        );
        $this->assertStringContainsString('No reaches matching', $htmlDefault);

        // hidden=1: the no_show reach is the single match → redirect.
        [, $statusHidden] = $this->captureSearch(
            fn() => handle_search_mode($this->pdo(), 'Gamma', '', 1, '')
        );
        $this->assertSame(302, $statusHidden);
    }
}
