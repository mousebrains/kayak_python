<?php

declare(strict_types=1);

require_once __DIR__ . '/FunctionalTestCase.php';
require_once __DIR__ . '/Fixtures.php';
require_once __DIR__ . '/../../php/includes/db.php';
require_once __DIR__ . '/../../php/includes/header.php';
require_once __DIR__ . '/../../php/includes/footer.php';
require_once __DIR__ . '/../../php/includes/gauge_search.php';

/**
 * In-process functional coverage for gauge_search.php
 * (handle_gauge_search + _search_gauges + _render_gauge_search_results).
 *
 * handle_gauge_search is `: never`; it ends via http_terminate() (302 on
 * a single match, 200 after rendering), both of which throw
 * HttpExitException under KAYAK_TEST. {@see captureSearch} captures the
 * buffered HTML and the status together.
 *
 * Covers: zero-match empty state, multi-match results table (rows with
 * and without a location), the single-match auto-redirect, and a match
 * via a non-name column (usgs_id) to exercise the LIKE-across-columns
 * query.
 */
final class GaugeSearchFunctionalTest extends FunctionalTestCase
{
    protected static function seedDatabase(PDO $db): void
    {
        // Two gauges sharing the "Willamette" token (multi-match), one with a
        // location and one without (drives the location ?? '' branch).
        Fixtures::gauge($db, [
            'name' => 'Willamette at Salem', 'location' => 'Salem',
        ]);
        Fixtures::gauge($db, [
            'name' => 'Willamette near Albany',  // no location column
        ]);
        // A gauge whose only match path is a non-name column (usgs_id).
        Fixtures::gauge($db, [
            'name' => 'Cryptic Station', 'location' => 'Bend', 'usgs_id' => '14181500',
        ]);
        // A uniquely-named gauge for the single-match redirect.
        Fixtures::gauge($db, ['name' => 'ZZZ Solo Gauge', 'location' => 'Nowhere']);
    }

    /**
     * Run the `: never` search handler, returning [rendered_html, status].
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

    public function testMultiMatchRendersResultsTable(): void
    {
        // "Willamette" matches two gauges → results table, no redirect.
        [$html, $status] = $this->captureSearch(
            fn() => handle_gauge_search($this->pdo(), 'Willamette')
        );
        $this->assertSame(200, $status);
        $this->assertStringContainsString('<h2>Gauge Search</h2>', $html);
        $this->assertStringContainsString('2 gauges matching', $html);
        $this->assertStringContainsString('&ldquo;Willamette&rdquo;', $html);
        $this->assertStringContainsString('Willamette at Salem', $html);
        $this->assertStringContainsString('Willamette near Albany', $html);
        // Row links to the detail page by its base-62 handle.
        $this->assertStringContainsString('/gauge.php?h=', $html);
        // The located gauge shows its location; the other's cell is blank.
        $this->assertStringContainsString('>Salem<', $html);
        $this->assertStringContainsString('Browse all gauges', $html);
    }

    public function testSingleMatchRedirects(): void
    {
        [$html, $status] = $this->captureSearch(
            fn() => handle_gauge_search($this->pdo(), 'Solo Gauge')
        );
        $this->assertSame(302, $status);
        $this->assertSame('', $html, 'Redirect path must not render a body');
    }

    public function testNoMatchEmptyState(): void
    {
        [$html, $status] = $this->captureSearch(
            fn() => handle_gauge_search($this->pdo(), 'no-such-gauge-zzz')
        );
        $this->assertSame(200, $status);
        $this->assertStringContainsString('No gauges matching', $html);
        $this->assertStringContainsString('&ldquo;no-such-gauge-zzz&rdquo;', $html);
        $this->assertStringNotContainsString('<table class="desc-table">', $html);
    }

    public function testMatchByNonNameColumn(): void
    {
        // The query LIKEs across station/usgs/cbtt/... ids too. A USGS id is
        // a unique match → redirect (exercises the usgs_id LIKE bind).
        [, $status] = $this->captureSearch(
            fn() => handle_gauge_search($this->pdo(), '14181500')
        );
        $this->assertSame(302, $status);
    }
}
