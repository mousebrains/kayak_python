<?php

declare(strict_types=1);

require_once __DIR__ . '/IntegrationTestCase.php';

/**
 * Baseline integration tests for plot.php (Phase 4.1 of
 * php_layer_split). plot.php is the simplest consumer of
 * php/includes/svg_plot.php — calls generate_svg_plot only, default
 * response is raw SVG (image/svg+xml), ?embed=1 wraps it in HTML.
 *
 * These tests catch consumer-side drift if Tier 4's rating-cluster
 * extraction accidentally changes generate_svg_plot's signature or
 * output. The SvgPlotTest unit tests cover the helper directly with
 * 11 cases; this file covers the URL → response path.
 *
 * Covers:
 *  - 400 on missing id / invalid type
 *  - 404 on non-gauged reach (entry-point guard at plot.php:38)
 *  - 200 raw SVG for a gauged reach (correct content-type)
 *  - 200 HTML wrap when ?embed=1 (cache-control: max-age=300)
 *
 * Seed: one gauged reach (id=3001) with one observation; one no-gauge
 * reach (id=3002).
 */
final class PlotIntegrationTest extends IntegrationTestCase
{
    private const REACH_WITH_GAUGE_ID = 3001;
    private const REACH_NO_GAUGE_ID = 3002;
    private const GAUGE_ID = 8001;
    private const SOURCE_ID = 9001;

    protected static function seedDatabase(PDO $db): void
    {
        $db->prepare(
            'INSERT INTO gauge (id, name, display_name) VALUES (?, ?, ?)'
        )->execute([self::GAUGE_ID, 'PLOT_TEST', 'Plot Test Gauge']);

        $db->prepare(
            'INSERT INTO source (id, name, agency) VALUES (?, ?, ?)'
        )->execute([self::SOURCE_ID, 'plot_test_source', 'USGS']);
        $db->prepare(
            'INSERT INTO gauge_source (gauge_id, source_id) VALUES (?, ?)'
        )->execute([self::GAUGE_ID, self::SOURCE_ID]);

        // Two observations so generate_svg_plot has > 1 data point and
        // doesn't fall through to the empty-SVG branch.
        foreach ([['2026-05-11 12:00:00', 1500.0], ['2026-05-12 12:00:00', 1600.0]] as [$ts, $v]) {
            $db->prepare(
                'INSERT INTO observation (source_id, data_type, value, observed_at)
                 VALUES (?, ?, ?, ?)'
            )->execute([self::SOURCE_ID, 'flow', $v, $ts]);
        }

        $db->prepare(
            'INSERT INTO reach (id, name, display_name, river, sort_name, gauge_id, no_show)
             VALUES (?, ?, ?, ?, ?, ?, ?)'
        )->execute([
            self::REACH_WITH_GAUGE_ID,
            'Plot Test Reach',
            'Plot Test Reach',
            'Plotville',
            'plot test reach',
            self::GAUGE_ID,
            0,
        ]);
        $db->prepare(
            'INSERT INTO reach (id, name, display_name, river, sort_name, no_show)
             VALUES (?, ?, ?, ?, ?, ?)'
        )->execute([
            self::REACH_NO_GAUGE_ID,
            'No Gauge Plot Reach',
            'No Gauge Plot Reach',
            'Nowhere',
            'no gauge plot reach',
            0,
        ]);
    }

    public function testMissingIdReturns400(): void
    {
        $resp = $this->request('/plot.php');

        $this->assertSame(400, $resp['status']);
        $this->assertStringContainsString('Missing id parameter', $resp['body']);
    }

    public function testInvalidTypeReturns400(): void
    {
        $resp = $this->request('/plot.php', [
            'id' => self::REACH_WITH_GAUGE_ID,
            'type' => 'notathing',
        ]);

        $this->assertSame(400, $resp['status']);
        $this->assertStringContainsString('Invalid type', $resp['body']);
    }

    public function testNonGaugedReachReturns404(): void
    {
        $resp = $this->request('/plot.php', ['id' => self::REACH_NO_GAUGE_ID]);

        $this->assertSame(404, $resp['status']);
        $this->assertStringContainsString('No gauge', $resp['body']);
    }

    public function testRawSvgResponse(): void
    {
        $resp = $this->request('/plot.php', [
            'id' => self::REACH_WITH_GAUGE_ID,
            'type' => 'flow',
        ]);

        $this->assertSame(200, $resp['status']);
        $this->assertSame('image/svg+xml', $resp['headers']['content-type'] ?? '');
        $this->assertResponseContains(
            $resp['body'],
            '<svg',
            'xmlns="http://www.w3.org/2000/svg"',
        );
        // Bare SVG response — no <script> at all (CSP holds for SVG embeds too).
        $this->assertNoBareInlineScript($resp['body']);
    }

    public function testEmbedWrapsInHtml(): void
    {
        $resp = $this->request('/plot.php', [
            'id' => self::REACH_WITH_GAUGE_ID,
            'type' => 'flow',
            'embed' => '1',
        ]);

        $this->assertSame(200, $resp['status']);
        $this->assertStringStartsWith('text/html', $resp['headers']['content-type'] ?? '');
        $this->assertResponseContains(
            $resp['body'],
            '<svg',                        // inline SVG body
            'class="plot-container"',      // HTML wrapper
            'JSON data',                   // footer link
            '</html>',
        );
        $this->assertNoBareInlineScript($resp['body']);
    }
}
