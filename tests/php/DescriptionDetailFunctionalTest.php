<?php

declare(strict_types=1);

require_once __DIR__ . '/FunctionalTestCase.php';
require_once __DIR__ . '/Fixtures.php';
require_once __DIR__ . '/../../php/includes/db.php';
require_once __DIR__ . '/../../php/includes/header.php';
require_once __DIR__ . '/../../php/includes/footer.php';
// validate.php (date_ts/validate_date) is pulled in by the description.php
// shim in production; the handler file itself doesn't require it, and the
// date-window plot path calls date_ts(), so the test loads it here.
require_once __DIR__ . '/../../php/includes/validate.php';
require_once __DIR__ . '/../../php/includes/description_detail.php';

/**
 * In-process functional coverage for description_detail.php — drives
 * handle_description_detail() directly so pcov counts the handler + its
 * file-private render helpers (readings table, date-form + plots, fields
 * + map, track-colour, data sources, guidebooks, footer).
 *
 * Seeded corpus (once per class), ordered by sort_name for deterministic
 * nav:
 *
 *   "ddd full"  → middle reach: linked gauge w/ coords + location, a source
 *                 per data-source branch (USGS station link, NWRFC via NWS,
 *                 a fetch_url source, a calc-expression source with a
 *                 resolvable `gauge::type` token, a plain source), recent
 *                 flow/gauge/temperature observations (drives the dual-axis
 *                 plot + temp plot), class range (okay band → green track),
 *                 latest_gauge_observation rows for every reading status
 *                 (stable / rising / falling / null-delta / unknown type),
 *                 geom + gradient profile, guidebook + AW row, notes.
 *   "fff bare"  → MIDDLE by sort_name (Prev + Next): no gauge, no coords, no
 *                 geom → no readings/plots/map/sources; empty description (no
 *                 h2 suffix); aw_id set so the guidebooks AW row still renders.
 *   "ggg blank" → display_name '' linked to BLANK_GAUGE; only exists so the
 *                 full reach's calc token resolves to a reach with no
 *                 display_name (the calc autolinker gauge-name fallback).
 *   "sss stale" → gauge has a usgs_id but no 'USGS'-agency source → the USGS
 *                 station link comes from the trailing unshown-station loop;
 *                 GAUGE-typed class range + flow-only reading → the track
 *                 colour loop continues past every band → default blue.
 *   "zzz tail"  → last by sort_name (no Next): gauge with NO location and NO
 *                 display_name (name-fallback in the Gauge field), gauge has
 *                 coords (map via point only, no geom), a low reading vs an
 *                 okay-band class range → 'low' track colour, no gradient
 *                 profile.
 *
 * Footer note: the editor-feature footer branches are gated by
 * current_editor(), which caches statically per process, so a single
 * in-process class can only observe one editor state. Those three branches
 * (maintainer / editor / anonymous) live in DescriptionFooterFunctionalTest,
 * where each test runs in its own process. This class therefore only
 * exercises the editor-feature-disabled footer (the default Config).
 */
final class DescriptionDetailFunctionalTest extends FunctionalTestCase
{
    private static int $fullId = 0;
    private static int $bareId = 0;
    private static int $tailId = 0;
    private static int $blankId = 0;
    private static int $staleId = 0;
    private static int $fullGaugeId = 0;

    private const GRADIENT_PROFILE_JSON =
        '{"samples":[{"d_mi":0.0,"grad_ft_per_mi":40.0,"w_mi":5.0,"significant":true},'
        . '{"d_mi":5.0,"grad_ft_per_mi":40.0,"w_mi":5.0,"significant":true}]}';

    protected static function seedDatabase(PDO $db): void
    {
        // Reference timestamps relative to "now" so the default 10-day window
        // and the 6h "current" check both see the seeded observations.
        $now = time();
        $ts = static fn(int $offsetSec): string => gmdate('Y-m-d H:i:s', $now + $offsetSec);

        // ---- Full gauge: coords + location, USGS + NWRFC station ids.
        self::$fullGaugeId = Fixtures::gauge($db, [
            'name' => 'SANTIAM_GAUGE',
            'display_name' => 'Santiam Gauge',
            'location' => 'at Mehama',
            'latitude' => 44.79,
            'longitude' => -122.62,
            'usgs_id' => '14183000',
            'nwsli_id' => 'MEHO3',
        ]);

        // ---- Sources feeding the full gauge, one per render branch:
        //   1. agency 'USGS'  → matches the USGS station_urls entry
        //   2. agency 'NWS'   → matches NWRFC via the NWS alias branch
        //   3. plain source with a fetch_url → fetch_url render branch
        //   4. calc-expression source → calc branch (token resolves to a reach)
        //   5. plain source, no agency, no url → the "—" fallback branch
        $usgsSrc = Fixtures::source($db, ['name' => 'santiam_usgs', 'agency' => 'USGS']);
        $nwsSrc  = Fixtures::source($db, ['name' => 'santiam_nws', 'agency' => 'NWS West']);

        // fetch_url-backed source.
        $fetchUrlId = (int) $db->query(
            "INSERT INTO fetch_url (url) VALUES ('https://waterservices.example/santiam') RETURNING id"
        )->fetchColumn();
        $fetchSrc = Fixtures::source($db, [
            'name' => 'santiam_fetch',
            'agency' => 'USACE',
            'fetch_url_id' => $fetchUrlId,
        ]);

        // calc-expression source. The autolinker regex /(\w+)::(\w+)::(\w+)/
        // treats the MIDDLE group as the gauge name, so the token is
        // <prefix>::<GAUGE_NAME>::<suffix>:
        //   flow::SANTIAM_GAUGE::mean → resolves to this reach (display_name
        //       set → anchor text uses the reach display_name).
        //   flow::BLANK_GAUGE::mean → resolves to a reach whose display_name
        //       is '' → the gauge-name fallback branch for the anchor text.
        //   flow::UNKNOWN_GAUGE::mean → no matching gauge → stays verbatim.
        $calcId = (int) $db->query(
            "INSERT INTO calc_expression (data_type, expression)
             VALUES ('flow', 'flow::SANTIAM_GAUGE::mean + flow::BLANK_GAUGE::mean + flow::UNKNOWN_GAUGE::mean')
             RETURNING id"
        )->fetchColumn();
        $calcSrc = Fixtures::source($db, [
            'name' => 'santiam_calc',
            'agency' => 'WKCC',
            'calc_expression_id' => $calcId,
        ]);

        $plainSrc = Fixtures::source($db, ['name' => 'santiam_plain']);

        foreach ([$usgsSrc, $nwsSrc, $fetchSrc, $calcSrc, $plainSrc] as $sid) {
            Fixtures::linkGaugeSource($db, self::$fullGaugeId, $sid);
        }

        // ---- Observations on the USGS source so the plots have data.
        // flow + gauge (drives the dual-axis rating plot when paired) + temp.
        for ($i = 0; $i < 6; $i++) {
            $when = $ts(-$i * 3600);                 // hourly, last 6h
            Fixtures::observation($db, $usgsSrc, ['data_type' => 'flow', 'observed_at' => $when, 'value' => 1000.0 + $i * 10]);
            Fixtures::observation($db, $usgsSrc, ['data_type' => 'gauge', 'observed_at' => $when, 'value' => 4.0 + $i * 0.1]);
            Fixtures::observation($db, $usgsSrc, ['data_type' => 'temperature', 'observed_at' => $when, 'value' => 48.0 + $i]);
        }

        // ---- latest_gauge_observation: one row per reading status branch.
        //   flow        → rising  (delta >= 0.5)
        //   gauge       → stable  (|delta| < 0.5)
        //   temperature → falling (delta < -0.5)
        //   inflow      → null delta (no status span)
        //   "humidity"  → unknown data_type → label/units fallback
        Fixtures::latestGaugeObservation($db, self::$fullGaugeId, ['data_type' => 'flow', 'value' => 1234.5, 'delta_per_hour' => 12.0, 'observed_at' => $ts(0)]);
        Fixtures::latestGaugeObservation($db, self::$fullGaugeId, ['data_type' => 'gauge', 'value' => 4.5, 'delta_per_hour' => 0.1, 'observed_at' => $ts(0)]);
        Fixtures::latestGaugeObservation($db, self::$fullGaugeId, ['data_type' => 'temperature', 'value' => 50.0, 'delta_per_hour' => -1.5, 'observed_at' => $ts(0)]);
        Fixtures::latestGaugeObservation($db, self::$fullGaugeId, ['data_type' => 'inflow', 'value' => 800.0, 'delta_per_hour' => null, 'observed_at' => $ts(0)]);
        Fixtures::latestGaugeObservation($db, self::$fullGaugeId, ['data_type' => 'humidity', 'value' => 55.0, 'delta_per_hour' => null, 'observed_at' => $ts(0)]);

        // ---- "ddd full" reach.
        self::$fullId = Fixtures::reach($db, [
            'name' => 'Delta Full Reach',
            'display_name' => 'Delta Full Reach',
            'sort_name' => 'ddd full',
            'river' => 'Santiam',
            'description' => 'Full description reach. Beta: https://example.com/beta',
            'difficulties' => 'Sieves below the bridge.',
            'notes' => "Park at the lot.\nhttps://example.org/notes",
            'gauge_id' => self::$fullGaugeId,
            'latitude_start' => 44.70,
            'longitude_start' => -122.80,
            'latitude_end' => 44.79,
            'longitude_end' => -122.62,
            'basin' => 'Santiam Basin',
            'region' => 'Cascades',
            'season' => 'Spring',
            'nature' => 'Pool-drop',
            'watershed_type' => 'Snowmelt',
            'scenery' => 'Forested',
            'remoteness' => 'Roadside',
            'features' => 'Waterfall',
            'elevation' => 1000.0,
            'elevation_lost' => 500.0,
            'length' => 7.0,
            'gradient' => 60.0,
            'max_gradient' => 120.0,
            'optimal_flow' => 1200.0,
            'aw_id' => 99,
            'geom' => '-122.80 44.70,-122.71 44.74,-122.62 44.79',
            'gradient_profile' => self::GRADIENT_PROFILE_JSON,
        ]);

        // okay band [800, 3000] flow — the flow reading 1234.5 is in range →
        // _compute_description_track_color returns the green 'okay' colour.
        Fixtures::reachClass($db, self::$fullId, [
            'name' => 'IV',
            'low' => 800.0,
            'low_data_type' => 'flow',
            'high' => 3000.0,
            'high_data_type' => 'flow',
        ]);

        // ---- "fff bare" reach: no gauge, empty description, aw_id set.
        self::$bareId = Fixtures::reach($db, [
            'name' => 'Foxtrot Bare Reach',
            'display_name' => 'Foxtrot Bare Reach',
            'sort_name' => 'fff bare',
            'description' => '',
            'aw_id' => 7,
        ]);

        // ---- Tail gauge: NO location, NO display_name, has coords (point map).
        $tailGauge = Fixtures::gauge($db, [
            'name' => 'TAIL_GAUGE',
            'display_name' => null,
            'location' => null,
            'latitude' => 45.10,
            'longitude' => -122.00,
        ]);
        // A low flow reading well below the okay band → 'low' track colour.
        Fixtures::latestGaugeObservation($db, $tailGauge, ['data_type' => 'flow', 'value' => 100.0, 'delta_per_hour' => 0.0, 'observed_at' => gmdate('Y-m-d H:i:s', $now)]);

        self::$tailId = Fixtures::reach($db, [
            'name' => 'Zulu Tail Reach',
            'display_name' => 'Zulu Tail Reach',
            'sort_name' => 'zzz tail',
            'description' => 'Tail reach with a point-only map.',
            'gauge_id' => $tailGauge,
        ]);
        // okay band [500, 2000] — the 100.0 flow reading is below low → 'low'.
        Fixtures::reachClass($db, self::$tailId, [
            'name' => 'III',
            'low' => 500.0,
            'low_data_type' => 'flow',
            'high' => 2000.0,
            'high_data_type' => 'flow',
        ]);

        // ---- "ggg blank": a reach whose display_name is '' linked to
        // BLANK_GAUGE, purely so the full reach's calc token
        // flow::BLANK_GAUGE::mean resolves to a reach with no display_name
        // (the gauge-name fallback branch in the calc autolinker). No coords,
        // no gauge readings → it never renders its own sections of interest.
        $blankGauge = Fixtures::gauge($db, ['name' => 'BLANK_GAUGE', 'display_name' => null]);
        self::$blankId = Fixtures::reach($db, [
            'name' => 'Golf Blank Reach',
            'display_name' => '',
            'sort_name' => 'ggg blank',
            'description' => 'Blank-display reach.',
            'gauge_id' => $blankGauge,
        ]);

        // ---- "sss stale": gauge has a usgs_id but NO source whose agency
        // contains 'USGS' (only a plain source) → the USGS station link is
        // appended by the trailing unshown-station loop rather than matched
        // inline. The reach carries a GAUGE-typed class range while the only
        // reading is flow, so _compute_description_track_color continues past
        // every band (data_type mismatch) and returns the default blue.
        $staleGauge = Fixtures::gauge($db, [
            'name' => 'STALE_GAUGE',
            'display_name' => 'Stale Gauge',
            'latitude' => 45.5,
            'longitude' => -121.5,
            'usgs_id' => '99999999',
        ]);
        $staleSrc = Fixtures::source($db, ['name' => 'stale_plain', 'agency' => 'USACE']);
        Fixtures::linkGaugeSource($db, $staleGauge, $staleSrc);
        Fixtures::latestGaugeObservation($db, $staleGauge, ['data_type' => 'flow', 'value' => 300.0, 'delta_per_hour' => 0.0, 'observed_at' => gmdate('Y-m-d H:i:s', $now)]);
        self::$staleId = Fixtures::reach($db, [
            'name' => 'Sierra Stale Reach',
            'display_name' => 'Sierra Stale Reach',
            'sort_name' => 'sss stale',
            'description' => 'Reach exercising the default track colour + trailing station link.',
            'gauge_id' => $staleGauge,
            'latitude_start' => 45.50,
            'longitude_start' => -121.55,
            'latitude_end' => 45.55,
            'longitude_end' => -121.50,
        ]);
        // Class range bound on GAUGE height (no flow bound) — the flow-only
        // reading can't match it → track colour stays the default.
        Fixtures::reachClass($db, self::$staleId, [
            'name' => 'IV',
            'low' => 3.0,
            'low_data_type' => 'gauge',
            'high' => 6.0,
            'high_data_type' => 'gauge',
        ]);

        // Link the full + tail + stale reaches to Oregon (Watershed line).
        $orId = (int) $db->query("SELECT id FROM state WHERE abbreviation = 'OR'")->fetchColumn();
        $db->prepare('INSERT INTO reach_state (reach_id, state_id) VALUES (?, ?)')->execute([self::$fullId, $orId]);
        $db->prepare('INSERT INTO reach_state (reach_id, state_id) VALUES (?, ?)')->execute([self::$tailId, $orId]);
        $db->prepare('INSERT INTO reach_state (reach_id, state_id) VALUES (?, ?)')->execute([self::$staleId, $orId]);

        // Guidebook + junction on the full reach.
        $gbId = (int) $db->query(
            "INSERT INTO guidebook (title, subtitle, edition, author, url, sort_order)
             VALUES ('Soggy Sneakers', 'Oregon Rivers', '4th', 'WKCC', 'https://book.example/ss4', 1)
             RETURNING id"
        )->fetchColumn();
        $db->prepare(
            'INSERT INTO reach_guidebook (reach_id, guidebook_id, page, run, url)
             VALUES (?, ?, ?, ?, ?)'
        )->execute([self::$fullId, $gbId, '88', 'Lower', 'https://book.example/ss4/lower']);
    }

    public function testFullReachRendersAllSections(): void
    {
        $html = $this->capture(
            fn() => handle_description_detail($this->pdo(), self::$fullId, null, null, 0)
        );

        // Header / h2 with description suffix.
        $this->assertStringContainsString('Delta Full Reach', $html);
        $this->assertStringContainsString(' -- ', $html);              // h2 description suffix
        $this->assertStringContainsString('Full description reach', $html);

        // Readings table — every status branch + unit/label fallbacks.
        $this->assertStringContainsString('class="readings-table"', $html);
        $this->assertStringContainsString('1,235 CFS', $html);          // flow value (rounded)
        $this->assertStringContainsString('<span class="rising">rising</span>', $html);
        $this->assertStringContainsString('<span class="stable">stable</span>', $html);
        $this->assertStringContainsString('<span class="falling">falling</span>', $html);
        $this->assertStringContainsString('Gage Height', $html);        // gauge label
        $this->assertStringContainsString('Inflow', $html);             // inflow label
        $this->assertStringContainsString('humidity', $html);           // unknown-type label fallback

        // Date form + at least one plot rendered (gauge present, data exists).
        $this->assertStringContainsString('name="start"', $html);
        $this->assertStringContainsString('plot-container', $html);

        // Fields table consolidated lines.
        $this->assertStringContainsString('Santiam Basin in Oregon, Cascades', $html);
        $this->assertStringContainsString('7.0 mi, gradient 60 ft/mi, max 120 ft/mi', $html);
        $this->assertStringContainsString('1,000 ft to 500 ft, loss 500 ft', $html);

        // Gauge field links to /gauge.php using the location label.
        $this->assertStringContainsString('/gauge.php?id=' . self::$fullGaugeId, $html);
        $this->assertStringContainsString('at Mehama', $html);

        // Coordinate trio + map + gradient profile.
        $this->assertStringContainsString('coord-trio', $html);
        $this->assertStringContainsString('Put-in:', $html);
        $this->assertStringContainsString('Take-out:', $html);
        $this->assertStringContainsString('id="feature-map"', $html);
        $this->assertStringContainsString('gradient-profile-container', $html);
        // okay band, flow in range → green track colour on the map div.
        $this->assertStringContainsString('data-track-color="#4caf50"', $html);

        // Autolinked description + notes.
        $this->assertStringContainsString('href="https://example.com/beta"', $html);
        $this->assertStringContainsString('href="https://example.org/notes"', $html);

        // Data Sources — each source-type branch.
        $this->assertStringContainsString('Data Sources', $html);
        $this->assertStringContainsString('USGS - 14183000', $html);            // USGS station link
        $this->assertStringContainsString('waterdata.usgs.gov', $html);
        $this->assertStringContainsString('NWRFC - MEHO3', $html);              // NWRFC via NWS alias
        $this->assertStringContainsString('https://waterservices.example/santiam', $html); // fetch_url branch
        $this->assertStringContainsString('Calculated:', $html);                // calc branch
        // calc token flow::SANTIAM_GAUGE::mean resolves to this reach (anchor
        // back to /description.php?id=<fullId>); the UNKNOWN_GAUGE token has
        // no matching gauge, so it stays verbatim.
        $this->assertStringContainsString('/description.php?id=' . self::$fullId . '"', $html);
        $this->assertStringContainsString('flow::UNKNOWN_GAUGE::mean', $html);

        // Guidebooks: AW row + the seeded book.
        $this->assertStringContainsString('Guidebooks', $html);
        $this->assertStringContainsString('American Whitewater', $html);
        $this->assertStringContainsString('river-detail/99/', $html);
        $this->assertStringContainsString('Soggy Sneakers — Oregon Rivers (4th)', $html);
        $this->assertStringContainsString('p. 88, run Lower', $html);

        // Footer (editor feature disabled by default → no Edit/Suggest button).
        $this->assertStringContainsString('Reach details', $html);
        $this->assertStringContainsString('/reach.php?id=' . self::$fullId, $html);
        $this->assertStringNotContainsString('Suggest an edit', $html);

        // Map present → leaflet + feature-map scripts; gradient script always.
        $this->assertStringContainsString('/static/leaflet.js', $html);
        $this->assertStringContainsString('/static/feature-map.js', $html);
        $this->assertStringContainsString('/static/gradient-profile.js', $html);
    }

    public function testFullReachIsFirstNoPrev(): void
    {
        // 'ddd full' sorts first among the three visible reaches
        // ('ddd' < 'fff' < 'zzz') → Prev is the grey span, Next is an anchor.
        $html = $this->capture(
            fn() => handle_description_detail($this->pdo(), self::$fullId, null, null, 0)
        );
        $this->assertStringContainsString('<span style="color:#999">&laquo; Prev</span>', $html);
        $this->assertStringContainsString('Next &raquo;</a>', $html);
        $this->assertStringContainsString('Reach 1 of 5', $html);
    }

    public function testBareReachIsMiddleAndRendersMinimally(): void
    {
        // 'fff bare' is the MIDDLE reach ('ddd' < 'fff' < 'zzz'), so it has
        // both a Prev (ddd) and a Next (zzz) anchor. Bare = no gauge → no
        // readings / plots / map / data-sources; empty description → no h2
        // suffix; aw_id set → guidebooks AW row still renders.
        $html = $this->capture(
            fn() => handle_description_detail($this->pdo(), self::$bareId, null, null, 0)
        );
        $this->assertStringContainsString('Foxtrot Bare Reach', $html);
        $this->assertStringContainsString('Reach 2 of 5', $html);
        // Middle reach → both nav links are anchors (prev !== false / next !== false).
        $this->assertStringContainsString('&laquo; Prev</a>', $html);
        $this->assertStringContainsString('Next &raquo;</a>', $html);
        // No gauge → these sections are all skipped.
        $this->assertStringNotContainsString('class="readings-table"', $html);
        $this->assertStringNotContainsString('Data Sources', $html);
        $this->assertStringNotContainsString('name="start"', $html);     // no date form
        $this->assertStringNotContainsString('id="feature-map"', $html);  // no map
        $this->assertStringNotContainsString('/static/feature-map.js', $html);
        // aw_id set → guidebooks AW row renders even with no guidebook rows.
        $this->assertStringContainsString('Guidebooks', $html);
        $this->assertStringContainsString('river-detail/7/', $html);
        // gradient-profile script ships regardless of map presence.
        $this->assertStringContainsString('/static/gradient-profile.js', $html);
    }

    public function testTailReachLowTrackColorAndNameFallback(): void
    {
        // 'zzz tail' sorts last → Next is the grey span (next === false).
        $html = $this->capture(
            fn() => handle_description_detail($this->pdo(), self::$tailId, null, null, 0)
        );
        $this->assertStringContainsString('<span style="color:#999">Next &raquo;</span>', $html);
        $this->assertStringContainsString('Reach 5 of 5', $html);

        // Gauge has no location AND no display_name → Gauge field falls back
        // to the bare gauge name.
        $this->assertStringContainsString('TAIL_GAUGE', $html);

        // Map renders from the gauge point alone (no geom on this reach).
        $this->assertStringContainsString('id="feature-map"', $html);
        // okay band [500,2000], flow reading 100 < low → 'low' (amber) track.
        $this->assertStringContainsString('data-track-color="#e8a735"', $html);
        // No gradient profile on this reach → no container.
        $this->assertStringNotContainsString('gradient-profile-container', $html);
        // No guidebooks + no aw_id → guidebooks section skipped.
        $this->assertStringNotContainsString('Guidebooks', $html);
    }

    public function testStaleReachDefaultTrackColorAndTrailingStationLink(): void
    {
        // STALE_GAUGE has a usgs_id but its only source's agency is 'USACE'
        // (no 'USGS' substring), so the USGS station link is emitted by the
        // trailing unshown-station loop, not matched inline. Its class range
        // is GAUGE-typed while the only reading is flow → the track-colour
        // loop continues past every band and returns the default blue.
        $html = $this->capture(
            fn() => handle_description_detail($this->pdo(), self::$staleId, null, null, 0)
        );
        // Trailing station link for the unmatched USGS id.
        $this->assertStringContainsString('USGS - 99999999', $html);
        // Map present (reach has coords) with the DEFAULT (blue) track colour.
        $this->assertStringContainsString('id="feature-map"', $html);
        $this->assertStringContainsString('data-track-color="#2196F3"', $html);
    }

    public function testCalcTokenGaugeNameFallback(): void
    {
        // The full reach's calc expression references BLANK_GAUGE, which is
        // linked to a reach whose display_name is '' — so the calc autolinker
        // anchor text falls back to the gauge name token. The anchor still
        // points at that reach's description page.
        $html = $this->capture(
            fn() => handle_description_detail($this->pdo(), self::$fullId, null, null, 0)
        );
        // Gauge-name fallback anchor text + link to the blank-display reach.
        $this->assertStringContainsString('>BLANK_GAUGE</a>', $html);
        $this->assertStringContainsString('/description.php?id=' . self::$blankId . '"', $html);

        // Rendering the blank reach directly exercises the handler's name
        // fallback: display_name '' → the page title uses reach.name.
        $blankHtml = $this->capture(
            fn() => handle_description_detail($this->pdo(), self::$blankId, null, null, 0)
        );
        $this->assertStringContainsString('Golf Blank Reach', $blankHtml);
    }

    public function testDateWindowedViewRendersDataSources(): void
    {
        // Explicit start/end → the non-default window branch of
        // gp_resolve_window + _render_description_date_form_and_plots.
        // Page still 200s and renders the sources section.
        $end = gmdate('Y-m-d');
        $start = gmdate('Y-m-d', time() - 5 * 86400);
        $html = $this->capture(
            fn() => handle_description_detail($this->pdo(), self::$fullId, $start, $end, 0)
        );
        $this->assertStringContainsString('Delta Full Reach', $html);
        $this->assertStringContainsString('Data Sources', $html);
        // The form echoes the supplied dates.
        $this->assertStringContainsString('value="' . $start . '"', $html);
        $this->assertStringContainsString('value="' . $end . '"', $html);
        $this->assertStringContainsString('</html>', $html);
    }

    public function testHiddenFlagThreadsThroughNav(): void
    {
        // hidden=1 — there are no no_show=1 reaches seeded, so this reach is
        // not in the hidden set: position 0 of 0, both nav links grey. This
        // drives the $hidden !== 0 href-suffix branch in the nav bar.
        $html = $this->capture(
            fn() => handle_description_detail($this->pdo(), self::$fullId, null, null, 1)
        );
        $this->assertStringContainsString('Reach 0 of 0', $html);
        $this->assertStringContainsString('<span style="color:#999">&laquo; Prev</span>', $html);
        $this->assertStringContainsString('<span style="color:#999">Next &raquo;</span>', $html);
    }

    // The footer's editor-button branches (maintainer / editor / anonymous)
    // are covered in DescriptionFooterFunctionalTest, where each runs in its
    // own process so current_editor()'s static cache starts cold per branch.

    public function testUnknownReachIs404(): void
    {
        // get_reach_or_404 renders the rich HTML error page then
        // http_terminate(404) — catchable via the test seam.
        $e = $this->captureExit(
            fn() => handle_description_detail($this->pdo(), 999999, null, null, 0)
        );
        $this->assertSame(404, $e->statusCode);
    }
}
