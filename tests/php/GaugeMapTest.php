<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/../../php/includes/gauge_map.php';

/**
 * Unit tests for php/includes/gauge_map.php.
 *
 * gm_render_map() echoes a <div> and reads $_SERVER['DOCUMENT_ROOT'] (for
 * the OSMB overlay-URL closure). We point DOCUMENT_ROOT at a temp dir and
 * capture stdout, then assert on the emitted data-* JSON. The geom-string
 * parser is reached via the $geom arg and via $reach_tracks.
 */
final class GaugeMapTest extends TestCase
{
    private string $docRoot = '';
    /** @var array<string, mixed> */
    private array $serverBackup;

    protected function setUp(): void
    {
        $this->serverBackup = $_SERVER;
        $this->docRoot = sys_get_temp_dir() . '/kayak-gm-test-' . bin2hex(random_bytes(4));
        mkdir($this->docRoot . '/static', 0777, true);
        $_SERVER['DOCUMENT_ROOT'] = $this->docRoot;
    }

    protected function tearDown(): void
    {
        $_SERVER = $this->serverBackup;
        foreach (glob($this->docRoot . '/static/*') ?: [] as $f) {
            @unlink($f);
        }
        @rmdir($this->docRoot . '/static');
        @rmdir($this->docRoot);
    }

    /** Run gm_render_map with the given args; return [returnValue, emittedHtml]. */
    private function render(
        array $points,
        ?string $geom = null,
        string $color = '#2196F3',
        array $reachTracks = [],
        ?int $gaugeId = null
    ): array {
        ob_start();
        $ret = gm_render_map($points, $geom, $color, $reachTracks, $gaugeId);
        $html = (string) ob_get_clean();
        return [$ret, $html];
    }

    /** Decode the JSON in a data-<name> attribute of the emitted div. */
    private function attr(string $html, string $name): mixed
    {
        if (!preg_match('/data-' . preg_quote($name, '/') . '="([^"]*)"/', $html, $m)) {
            $this->fail("attribute data-$name not found in: $html");
        }
        return json_decode(html_entity_decode($m[1], ENT_QUOTES, 'UTF-8'), true);
    }

    // --- gm_head_links ----------------------------------------------------

    public function test_head_links_emits_leaflet_css(): void
    {
        $this->assertStringContainsString('leaflet.css', gm_head_links());
        $this->assertStringContainsString('<link rel="stylesheet"', gm_head_links());
    }

    // --- empty / no-op ----------------------------------------------------

    public function test_returns_false_and_emits_nothing_when_all_empty(): void
    {
        [$ret, $html] = $this->render([], null);
        $this->assertFalse($ret);
        $this->assertSame('', $html);
    }

    public function test_returns_false_with_empty_geom_string(): void
    {
        [$ret, $html] = $this->render([], '');
        $this->assertFalse($ret);
        $this->assertSame('', $html);
    }

    // --- points only ------------------------------------------------------

    public function test_points_only_emits_div(): void
    {
        [$ret, $html] = $this->render(['Gauge' => '44.56,-123.25']);
        $this->assertTrue($ret);
        $this->assertStringContainsString('id="feature-map"', $html);
        $this->assertSame(['Gauge' => '44.56,-123.25'], $this->attr($html, 'points'));
        // No track when geom omitted.
        $this->assertSame('null', json_encode($this->attr($html, 'track')));
    }

    // --- geom parsing -----------------------------------------------------

    public function test_geom_parsed_into_lat_lon_points(): void
    {
        // Input is "lon lat,lon lat,..."; output points are [lat, lon].
        $geom = '-123.25 44.56,-123.20 44.60';
        [$ret, $html] = $this->render(['Put-in' => '44.56,-123.25'], $geom);
        $this->assertTrue($ret);
        $track = $this->attr($html, 'track');
        $this->assertSame([[44.56, -123.25], [44.6, -123.2]], $track);
    }

    public function test_geom_malformed_pairs_skipped(): void
    {
        // First pair has 3 tokens, last has 1 — both dropped; only the
        // well-formed middle pair survives.
        $geom = '-123.2 44.5 99,-123.1 44.6,-123.0';
        [, $html] = $this->render(['x' => '44.5,-123.2'], $geom);
        $this->assertSame([[44.6, -123.1]], $this->attr($html, 'track'));
    }

    public function test_geom_all_malformed_yields_null_track(): void
    {
        // No valid 2-token pair → parse returns [] → track stays null, but
        // the points make the map render anyway.
        [$ret, $html] = $this->render(['x' => '44.5,-123.2'], 'garbage,more-garbage');
        $this->assertTrue($ret);
        $this->assertNull($this->attr($html, 'track'));
    }

    // --- reach tracks -----------------------------------------------------

    public function test_reach_tracks_payload_with_metadata(): void
    {
        $tracks = [[
            'id' => 7,
            'name' => 'Wilson',
            'geom' => '-123.2 44.5,-123.1 44.6',
            'location' => 'Coast',
            'classes' => 'III-IV',
            'status' => 'okay',
        ]];
        [$ret, $html] = $this->render([], null, '#2196F3', $tracks);
        $this->assertTrue($ret);
        $payload = $this->attr($html, 'reach-tracks');
        $this->assertCount(1, $payload);
        $this->assertSame(7, $payload[0]['id']);
        $this->assertSame('Wilson', $payload[0]['name']);
        $this->assertSame('okay', $payload[0]['status']);
        $this->assertSame([[44.5, -123.2], [44.6, -123.1]], $payload[0]['points']);
    }

    public function test_reach_tracks_metadata_defaults(): void
    {
        // Missing location/classes/status fall back to ''/''/'unknown'.
        $tracks = [['id' => 1, 'name' => 'R', 'geom' => '-1 1,-2 2']];
        [, $html] = $this->render([], null, '#2196F3', $tracks);
        $payload = $this->attr($html, 'reach-tracks');
        $this->assertSame('', $payload[0]['location']);
        $this->assertSame('', $payload[0]['classes']);
        $this->assertSame('unknown', $payload[0]['status']);
    }

    public function test_reach_track_with_fewer_than_two_points_dropped(): void
    {
        // A single-point geom can't form a polyline → excluded from the
        // reach-tracks payload. A non-empty $reach_tracks still trips the
        // render guard, so the div emits with an empty tracks array.
        $tracks = [['id' => 1, 'name' => 'R', 'geom' => '-123.2 44.5']];
        [$ret, $html] = $this->render([], null, '#2196F3', $tracks);
        $this->assertTrue($ret);
        $this->assertStringContainsString('id="feature-map"', $html);
        $this->assertSame([], $this->attr($html, 'reach-tracks'));
    }

    // --- gauge id + osmb overlay urls ------------------------------------

    public function test_gauge_id_attr_emitted_when_set(): void
    {
        [, $html] = $this->render(['Gauge' => '44.5,-123.2'], null, '#2196F3', [], 42);
        $this->assertStringContainsString('data-gauge-id="42"', $html);
    }

    public function test_gauge_id_attr_absent_when_null(): void
    {
        [, $html] = $this->render(['Gauge' => '44.5,-123.2']);
        $this->assertStringNotContainsString('data-gauge-id', $html);
    }

    public function test_osmb_url_empty_when_file_missing(): void
    {
        // No overlay files staged → the three osmb-* attrs are empty strings.
        [, $html] = $this->render(['Gauge' => '44.5,-123.2']);
        $this->assertStringContainsString('data-osmb-obstructions-url=""', $html);
        $this->assertStringContainsString('data-osmb-dams-url=""', $html);
        $this->assertStringContainsString('data-osmb-access-url=""', $html);
    }

    public function test_osmb_url_versioned_when_file_present(): void
    {
        // Stage one overlay file → its attr carries /static/<name>?v=<mtime>.
        file_put_contents($this->docRoot . '/static/osmb-dams.geojson', '{}');
        [, $html] = $this->render(['Gauge' => '44.5,-123.2']);
        $this->assertMatchesRegularExpression(
            '#data-osmb-dams-url="/static/osmb-dams\.geojson\?v=\d+"#',
            $html
        );
        // The un-staged ones stay empty.
        $this->assertStringContainsString('data-osmb-obstructions-url=""', $html);
    }
}
