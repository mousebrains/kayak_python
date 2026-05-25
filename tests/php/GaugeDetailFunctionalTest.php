<?php

declare(strict_types=1);

require_once __DIR__ . '/FunctionalTestCase.php';
require_once __DIR__ . '/Fixtures.php';
require_once __DIR__ . '/../../php/includes/db.php';
require_once __DIR__ . '/../../php/includes/header.php';
require_once __DIR__ . '/../../php/includes/footer.php';
// gp_resolve_window() (in gauge_plots.php, pulled in by gauge_detail.php)
// calls date_ts() on the explicit-window path; the handler file itself
// doesn't require validate.php, so the test loads it here.
require_once __DIR__ . '/../../php/includes/validate.php';
require_once __DIR__ . '/../../php/includes/gauge_detail.php';

/**
 * In-process functional coverage for gauge_detail.php — drives
 * handle_gauge_detail() directly so pcov counts the handler + every
 * file-private render helper (readings table, stale/empty banner,
 * date-form + plots, map, details table, associated sources/reaches,
 * regression section, footer). The subprocess integration tests can't
 * be seen by pcov, hence this in-process approach (mirrors the
 * reach_detail / description_detail sibling suites that reach ~98%).
 *
 * Seeded corpus (once per class). Gauges use distinct ids so the
 * scenarios don't cross-contaminate; navigation is by ascending gauge
 * id (no sort_name / no_show for gauges), so the seed order below also
 * fixes the prev/next boundaries.
 *
 *   $fullId  → the centrepiece happy path: display_name set, coords +
 *              all metadata fields (NWSLI/CBTT/GEOS/NWS/SNOTEL/USGS,
 *              elevation, drainage, bank_full, flood_stage), paired
 *              flow+gauge observations (≥2 rating bins → dual-axis plot)
 *              plus temperature, latest_gauge_observation rows for every
 *              status branch (rising / falling / stable / null-delta /
 *              unknown type), three associated sources (counts + latest +
 *              calc provenance), three associated reaches with class
 *              thresholds driving low / okay / high statuses + a geom
 *              track for the map, and a regression "target" role.
 *   $predId  → consumed as a predictor by $fullId's regression calc
 *              (its name appears in the calc time_expression) → the
 *              regression "predictor" framing. Has coords + a reach with
 *              a flow threshold → 'high' status; no display_name (name
 *              fallback in nav/header is exercised by the bare gauge).
 *   $bareId  → no readings, no coords, no sources, no reaches → the
 *              empty-readings banner, "No associated sources/reaches",
 *              no map, no plots, no footer scripts; null display_name →
 *              header/h2 name fallback.
 *   $staleId → a single reading > 7 days old → the yellow stale banner;
 *              reach geom but NO gauge coords → the map renders from the
 *              reach track alone (the no-coords-with-tracks branch).
 */
final class GaugeDetailFunctionalTest extends FunctionalTestCase
{
    private static int $gaugeId = 0;   // back-compat alias for the proof tests
    private static int $fullId = 0;
    private static int $predId = 0;
    private static int $bareId = 0;
    private static int $staleId = 0;
    private static int $regEdgeId = 0;
    private static int $lastId = 0;     // highest gauge id (nav "no next" boundary)

    /** A temp DOCUMENT_ROOT so the regression artifact files live off-repo. */
    private static string $docRoot = '';

    private const REGRESSION_SLUG = 'fullgauge_pred';

    protected static function seedDatabase(PDO $db): void
    {
        $now = time();
        $ts = static fn(int $offsetSec): string => gmdate('Y-m-d H:i:s', $now + $offsetSec);

        // ---- Predictor gauge (lower id → also the nav "first" gauge). It is
        // referenced by the full gauge's regression calc time_expression and
        // carries a reach whose flow threshold the gauge reading exceeds.
        self::$predId = Fixtures::gauge($db, [
            'name' => 'PRED_GAUGE',
            'display_name' => null,                 // name-fallback in nav anchors
            'river' => 'Predictor River',
            'latitude' => 45.10,
            'longitude' => -122.40,
        ]);
        Fixtures::latestGaugeObservation($db, self::$predId, [
            'data_type' => 'flow', 'value' => 5000.0, 'delta_per_hour' => 2.0, 'observed_at' => $ts(0),
        ]);
        $predReach = Fixtures::reach($db, [
            'name' => 'Predictor Reach',
            'display_name' => 'Predictor Reach',
            'sort_name' => 'predictor reach',
            'river' => 'Predictor River',
            'description' => 'High-water reach.',
            'gauge_id' => self::$predId,
        ]);
        // okay band [800, 3000] flow; reading 5000 > high → 'high' status.
        Fixtures::reachClass($db, $predReach, [
            'name' => 'V', 'low' => 800.0, 'low_data_type' => 'flow',
            'high' => 3000.0, 'high_data_type' => 'flow',
        ]);

        // _classify_reach_status data_type-mismatch branches. The predictor
        // gauge has ONLY a flow reading, so:
        //   • a GAUGE-typed threshold → the flow candidate's low_data_type
        //     mismatch (line 266 continue); then the gauge + inflow candidates
        //     are absent (line 258 continue) → 'unknown'.
        //   • a flow-low / gauge-HIGH threshold → low matches but the high
        //     data_type mismatches (line 269 continue) → 'unknown'.
        $gaugeTypedReach = Fixtures::reach($db, [
            'name' => 'Gauge-typed Reach',
            'display_name' => 'Gauge-typed Reach',
            'sort_name' => 'gauge-typed reach',
            'description' => 'Class bound on gauge height; gauge has flow only.',
            'gauge_id' => self::$predId,
        ]);
        Fixtures::reachClass($db, $gaugeTypedReach, [
            'name' => 'IV', 'low' => 4.0, 'low_data_type' => 'gauge',
            'high' => 9.0, 'high_data_type' => 'gauge',
        ]);
        $mixedTypedReach = Fixtures::reach($db, [
            'name' => 'Mixed-typed Reach',
            'display_name' => 'Mixed-typed Reach',
            'sort_name' => 'mixed-typed reach',
            'description' => 'Flow low bound, gauge high bound.',
            'gauge_id' => self::$predId,
        ]);
        // low (flow) ≤ high numerically to satisfy ck_reach_class_low_le_high;
        // the high_data_type ('gauge') still mismatches the flow classify path.
        Fixtures::reachClass($db, $mixedTypedReach, [
            'name' => 'IV+', 'low' => 1000.0, 'low_data_type' => 'flow',
            'high' => 1500.0, 'high_data_type' => 'gauge',
        ]);

        // ---- Full gauge: every metadata field + readings + sources + reaches.
        self::$gaugeId = self::$fullId = Fixtures::gauge($db, [
            'name' => 'FULL_GAUGE',
            'display_name' => 'Full Gauge Display',  // display_name branch (line 40)
            'river' => 'Test River',
            'location' => 'at Testville',
            'state' => 'OR',
            'latitude' => 44.7400,
            'longitude' => -122.1500,
            'elevation' => 1200.0,
            'drainage_area' => 250.0,
            'bank_full' => 6.50,
            'flood_stage' => 9.00,
            'station_id' => 'STN123',
            'usgs_id' => '14183000',
            'cbtt_id' => 'CBTT1',
            'geos_id' => 'GEOS1',
            'nws_id' => 'NWS1',
            'nwsli_id' => 'MEHO3',                   // → NWRFC flowplot anchor
            'snotel_id' => 'SNO1',
        ]);

        // Sources feeding the full gauge:
        //   1. a plain source with observations (obs_count + latest_at)
        //   2. a calc-expression source whose calc carries a provenance_slug
        //      → the regression "target" role reads the slug from $sources.
        //   3. a second plain source (no observations → obs_count 0, no agency)
        $obsSrc = Fixtures::source($db, ['name' => 'full_obs_src', 'agency' => 'USGS']);

        $calcId = (int) $db->query(
            "INSERT INTO calc_expression (data_type, expression, provenance_slug)
             VALUES ('flow', 'flow::FULL_GAUGE::mean', '" . self::REGRESSION_SLUG . "')
             RETURNING id"
        )->fetchColumn();
        $calcSrc = Fixtures::source($db, [
            'name' => 'full_calc_src', 'agency' => 'WKCC', 'calc_expression_id' => $calcId,
        ]);
        $emptySrc = Fixtures::source($db, ['name' => 'full_empty_src']); // no agency, 0 obs

        foreach ([$obsSrc, $calcSrc, $emptySrc] as $sid) {
            Fixtures::linkGaugeSource($db, self::$fullId, $sid);
        }

        // Paired flow + gauge observations on the obs source so
        // derive_rating_lookup yields ≥2 monotone bins → the dual-axis plot.
        // Spread gauge_ft over a wide range so binning produces >1 bin, and
        // make flow strictly increase with gauge so the monotone filter keeps
        // every bin. Recent (last 6h) so the default-view 6h "current" gate
        // and the 10-day window both include them.
        for ($i = 0; $i < 6; $i++) {
            $when = $ts(-$i * 3600);
            Fixtures::observation($db, $obsSrc, ['data_type' => 'flow', 'observed_at' => $when, 'value' => 1000.0 + $i * 300]);
            Fixtures::observation($db, $obsSrc, ['data_type' => 'gauge', 'observed_at' => $when, 'value' => 3.0 + $i * 0.5]);
            Fixtures::observation($db, $obsSrc, ['data_type' => 'temperature', 'observed_at' => $when, 'value' => 48.0 + $i]);
        }

        // latest_gauge_observation — one row per readings-table status branch.
        //   flow        → rising  (delta ≥ 0.5)
        //   gauge       → stable  (|delta| < 0.5)
        //   temperature → falling (delta < -0.5)
        //   inflow      → null delta (no status span; flow-units delta path)
        //   "humidity"  → unknown data_type → label/unit fallback; empty time
        Fixtures::latestGaugeObservation($db, self::$fullId, ['data_type' => 'flow', 'value' => 1234.5, 'delta_per_hour' => 12.0, 'observed_at' => $ts(0)]);
        Fixtures::latestGaugeObservation($db, self::$fullId, ['data_type' => 'gauge', 'value' => 4.5, 'delta_per_hour' => 0.1, 'observed_at' => $ts(0)]);
        Fixtures::latestGaugeObservation($db, self::$fullId, ['data_type' => 'temperature', 'value' => 50.0, 'delta_per_hour' => -1.5, 'observed_at' => $ts(0)]);
        Fixtures::latestGaugeObservation($db, self::$fullId, ['data_type' => 'inflow', 'value' => 800.0, 'delta_per_hour' => null, 'observed_at' => $ts(0)]);
        // Unknown type with an EMPTY observed_at → the time 'N/A' branch.
        Fixtures::latestGaugeObservation($db, self::$fullId, ['data_type' => 'humidity', 'value' => 55.0, 'delta_per_hour' => null, 'observed_at' => '']);

        // Associated reaches with class thresholds → low / okay / high statuses.
        // okay band [800, 3000] flow vs the full gauge's flow reading 1234.5:
        //   in range → 'okay'.
        $okayReach = Fixtures::reach($db, [
            'name' => 'Okay Reach',
            'display_name' => 'Okay Reach',
            'sort_name' => 'aaa okay',
            'river' => 'Test River',
            'description' => 'In-range reach.',
            'length' => 8.0,
            'gauge_id' => self::$fullId,
            'geom' => '-122.20 44.70,-122.15 44.74,-122.10 44.78',  // LineString → map track
        ]);
        Fixtures::reachClass($db, $okayReach, [
            'name' => 'IV', 'low' => 800.0, 'low_data_type' => 'flow',
            'high' => 3000.0, 'high_data_type' => 'flow',
        ]);
        // low band: high threshold far below 1234.5 won't help; set a low bound
        // above the reading so 1234.5 < low → 'low'.
        $lowReach = Fixtures::reach($db, [
            'name' => 'Low Reach',
            'display_name' => 'Low Reach',
            'sort_name' => 'bbb low',
            'river' => 'Test River',
            'description' => 'Needs-more-water reach.',
            'length' => 3.5,
            'gauge_id' => self::$fullId,
        ]);
        Fixtures::reachClass($db, $lowReach, [
            'name' => 'III', 'low' => 2000.0, 'low_data_type' => 'flow',
            'high' => 6000.0, 'high_data_type' => 'flow',
        ]);
        // unknown: a reach whose only class threshold is GAUGE-typed while the
        // gauge has a gauge reading of 4.5 — that's in [4.0, 9.0] → 'okay' would
        // fire. To get 'unknown' instead, give it a class with NO bounds (the
        // empty-thresholds skip) so classify falls through to 'unknown'.
        $unkReach = Fixtures::reach($db, [
            'name' => 'Unknown Reach',
            'display_name' => '',                    // empty → COALESCE name fallback
            'sort_name' => 'ccc unknown',
            'river' => 'Test River',
            'description' => 'No flow bounds reach.',
            'gauge_id' => self::$fullId,
        ]);
        Fixtures::reachClass($db, $unkReach, ['name' => 'II']);  // no low/high

        // ---- Bare gauge: no readings, no coords, no sources, no reaches.
        self::$bareId = Fixtures::gauge($db, [
            'name' => 'BARE_GAUGE',
            'display_name' => null,                  // name fallback in header/h2
        ]);

        // ---- Stale gauge: one reading > 7 days old + a reach with geom but
        // the gauge has NO coords → map renders from the reach track alone.
        self::$staleId = Fixtures::gauge($db, [
            'name' => 'STALE_GAUGE',
            'display_name' => 'Stale Gauge',
            // no coords
        ]);
        Fixtures::latestGaugeObservation($db, self::$staleId, [
            'data_type' => 'flow', 'value' => 400.0, 'delta_per_hour' => -0.8,
            'observed_at' => gmdate('Y-m-d H:i:s', $now - 30 * 86400),  // 30 days old
        ]);
        $staleReach = Fixtures::reach($db, [
            'name' => 'Stale Reach',
            'display_name' => 'Stale Reach',
            'sort_name' => 'stale reach',
            'description' => 'Reach with geom but a coord-less gauge.',
            'gauge_id' => self::$staleId,
            'geom' => '-121.55 45.50,-121.50 45.55',
        ]);
        // okay band so the reach gets a non-unknown status badge for the table.
        Fixtures::reachClass($db, $staleReach, [
            'name' => 'III', 'low' => 100.0, 'low_data_type' => 'flow',
            'high' => 2000.0, 'high_data_type' => 'flow',
        ]);

        // ---- Regression PREDICTOR wiring: a second calc gauge whose
        // time_expression references FULL_GAUGE by name (::FULL_GAUGE::) so the
        // full gauge gets the "used as a predictor" framing, AND the calc
        // carries the SAME provenance_slug so the single regression section
        // gets BOTH the target and predictor intro sentences.
        $predCalcId = (int) $db->query(
            "INSERT INTO calc_expression (data_type, expression, time_expression, provenance_slug)
             VALUES ('flow', 'flow::FULL_GAUGE::mean * 1.1',
                     'flow::FULL_GAUGE::mean * 1.1', '" . self::REGRESSION_SLUG . "')
             RETURNING id"
        )->fetchColumn();
        $predCalcSrc = Fixtures::source($db, [
            'name' => 'pred_calc_src', 'agency' => 'WKCC', 'calc_expression_id' => $predCalcId,
        ]);
        Fixtures::linkGaugeSource($db, self::$predId, $predCalcSrc);

        self::seedRegressionEdgeGauge($db);

        // ---- Regression artifact files in a temp DOCUMENT_ROOT (off-repo).
        self::$docRoot = sys_get_temp_dir() . '/kayak_gauge_docroot_' . getmypid();
        $regDir = self::$docRoot . '/static/regression';
        if (!is_dir($regDir)) {
            mkdir($regDir, 0777, true);
        }
        file_put_contents($regDir . '/' . self::REGRESSION_SLUG . '.svg', "<svg></svg>");
        // coefs include a large (|v|>=1 → number_format) and a small (<1 →
        // sprintf %.6g) coefficient so both formatting branches run. window
        // has two entries → the window suffix branch.
        file_put_contents(
            $regDir . '/' . self::REGRESSION_SLUG . '.json',
            (string) json_encode([
                'predictors' => ['14999999', '15000000'],
                'coefs' => [
                    ['name' => 'intercept', 'value' => 12.3456, 'se' => 1.2345],
                    ['name' => 'slope', 'value' => 0.0034, 'se' => 0.00012],
                    'not-an-array-skip',                       // → the !is_array($c) continue
                ],
                'r2' => 0.9876,
                'rmse' => 42.0,
                'n' => 365,
                'window' => ['2020-01-01', '2024-12-31'],
            ])
        );
        // Also drop a feature-map.js so the footer's filemtime() succeeds
        // (the != false && != 0 branch).
        if (!is_file(self::$docRoot . '/static/feature-map.js')) {
            file_put_contents(self::$docRoot . '/static/feature-map.js', '// stub');
        }

        // ---- Regression-edge artifacts (consumed by $regEdgeId):
        //   regedge_ok    → valid; JSON has NO predictors + a 1-entry window →
        //                   the 'another gauge' label (694) + empty window (735).
        //   regedge_pred  → valid; predictor-only slug (not among $regEdgeId's
        //                   own sources) → the "not yet in by_slug" branch (648).
        //   regedge_bad   → valid charset but the .json holds no `coefs` (679).
        //   (regedge_nofiles + bad.slug get NO files — 671 / 664.)
        file_put_contents($regDir . '/regedge_ok.svg', '<svg></svg>');
        file_put_contents($regDir . '/regedge_ok.json', (string) json_encode([
            // no 'predictors' key → 'another gauge'; 1-entry window → no suffix.
            'coefs' => [['name' => 'slope', 'value' => 2.5, 'se' => 0.5]],
            'r2' => 0.5, 'rmse' => 10.0, 'n' => 12, 'window' => ['2021-01-01'],
        ]));
        file_put_contents($regDir . '/regedge_pred.svg', '<svg></svg>');
        file_put_contents($regDir . '/regedge_pred.json', (string) json_encode([
            'predictors' => [], 'coefs' => [['name' => 'b', 'value' => 1.0, 'se' => 0.1]],
            'r2' => 0.6, 'rmse' => 5.0, 'n' => 30, 'window' => ['2022-01-01', '2022-12-31'],
        ]));
        file_put_contents($regDir . '/regedge_bad.svg', '<svg></svg>');
        file_put_contents($regDir . '/regedge_bad.json', '[]');   // is_array but no coefs → 679
    }

    /**
     * A gauge wired to exercise the regression section's defensive edges:
     * an invalid slug (664), a valid slug with no artifact files (671), a
     * valid slug whose JSON lacks `coefs` (679), a target slug with no
     * predictors / a 1-entry window (694 / 735), and a predictor-only slug
     * the gauge isn't itself a target of (648).
     */
    private static function seedRegressionEdgeGauge(PDO $db): void
    {
        self::$regEdgeId = Fixtures::gauge($db, [
            'name' => 'REGEDGE_GAUGE', 'display_name' => 'RegEdge Gauge',
        ]);

        // calc sources on this gauge → each registers a TARGET slug.
        $mk = static function (PDO $db, string $slug): int {
            return (int) $db->query(
                "INSERT INTO calc_expression (data_type, expression, provenance_slug)
                 VALUES ('flow', 'flow::X::mean', " . $db->quote($slug) . ") RETURNING id"
            )->fetchColumn();
        };
        foreach (['regedge_ok', 'bad.slug', 'regedge_nofiles', 'regedge_bad'] as $slug) {
            $cid = $mk($db, $slug);
            $sid = Fixtures::source($db, [
                'name' => 'regedge_src_' . md5($slug), 'agency' => 'WKCC', 'calc_expression_id' => $cid,
            ]);
            Fixtures::linkGaugeSource($db, self::$regEdgeId, $sid);
        }

        // Predictor-only slug: a SEPARATE calc whose time_expression references
        // REGEDGE_GAUGE by name and carries slug 'regedge_pred' (NOT one of the
        // gauge's own source slugs), linked to the bare-ish gauge so the
        // predictor JOIN yields a calc_gauge row.
        $predOnlyCalc = (int) $db->query(
            "INSERT INTO calc_expression (data_type, expression, time_expression, provenance_slug)
             VALUES ('flow', 'flow::REGEDGE_GAUGE::mean', 'flow::REGEDGE_GAUGE::mean', 'regedge_pred')
             RETURNING id"
        )->fetchColumn();
        self::$lastId = Fixtures::gauge($db, ['name' => 'REGEDGE_HOST', 'display_name' => 'RegEdge Host']);
        $predOnlySrc = Fixtures::source($db, [
            'name' => 'regedge_predonly_src', 'agency' => 'WKCC', 'calc_expression_id' => $predOnlyCalc,
        ]);
        Fixtures::linkGaugeSource($db, self::$lastId, $predOnlySrc);
    }

    public static function tearDownAfterClass(): void
    {
        // Best-effort cleanup of the temp DOCUMENT_ROOT tree.
        if (self::$docRoot !== '' && is_dir(self::$docRoot)) {
            $it = new RecursiveIteratorIterator(
                new RecursiveDirectoryIterator(self::$docRoot, FilesystemIterator::SKIP_DOTS),
                RecursiveIteratorIterator::CHILD_FIRST
            );
            foreach ($it as $f) {
                $f->isDir() ? @rmdir($f->getPathname()) : @unlink($f->getPathname());
            }
            @rmdir(self::$docRoot);
        }
        self::$docRoot = '';
        parent::tearDownAfterClass();
    }

    /** Point DOCUMENT_ROOT at the temp tree holding the regression artifacts. */
    protected function setUp(): void
    {
        parent::setUp();
        if (self::$docRoot !== '') {
            $_SERVER['DOCUMENT_ROOT'] = self::$docRoot;
        }
    }

    // ---- Proof-of-harness smoke tests (kept from the original 2-test file).

    public function testRendersGaugePage(): void
    {
        $html = $this->capture(fn() => handle_gauge_detail($this->pdo(), self::$fullId, null, null));
        $this->assertStringContainsString('Test River', $html);
        $this->assertStringContainsString('Okay Reach', $html);
    }

    public function testUnknownGaugeIs404(): void
    {
        $e = $this->captureExit(fn() => handle_gauge_detail($this->pdo(), 999999, null, null));
        $this->assertSame(404, $e->statusCode);
    }

    // ---- The fully-populated gauge: readings, plots, map, details,
    // sources, reaches, regression.

    public function testFullGaugeRendersEverything(): void
    {
        $html = $this->capture(fn() => handle_gauge_detail($this->pdo(), self::$fullId, null, null));

        // display_name branch (line 40) → header + h2 use the display name.
        $this->assertStringContainsString('Full Gauge Display', $html);
        $this->assertStringContainsString('<h2>Full Gauge Display</h2>', $html);

        // Readings table — every status branch + unit/label fallbacks.
        $this->assertStringContainsString('class="readings-table"', $html);
        $this->assertStringContainsString('1,235 CFS', $html);                    // flow value (rounded)
        $this->assertStringContainsString('<span class="rising">rising</span>', $html);
        $this->assertStringContainsString('<span class="stable">stable</span>', $html);
        $this->assertStringContainsString('<span class="falling">falling</span>', $html);
        $this->assertStringContainsString('Gage Height', $html);                  // gauge label
        $this->assertStringContainsString('Inflow', $html);                       // inflow label
        $this->assertStringContainsString('humidity', $html);                     // unknown-type fallback
        // The unknown-type row has an empty observed_at → time cell 'N/A'.
        $this->assertStringContainsString('<td>N/A</td>', $html);

        // Date form + dual-axis plot (paired flow+gauge → ≥2 rating bins).
        $this->assertStringContainsString('name="start"', $html);
        $this->assertStringContainsString('plot-container', $html);
        $this->assertStringContainsString('Gage Height', $html);                  // dual-axis title fragment

        // Map: gauge coords present + a reach LineString track.
        $this->assertStringContainsString('id="feature-map"', $html);
        $this->assertStringContainsString('data-reach-tracks=', $html);

        // Gauge details table — all the optional metadata fields.
        $this->assertStringContainsString('class="desc-table"', $html);
        $this->assertStringContainsString('STN123', $html);                       // Station ID
        $this->assertStringContainsString('14183000', $html);                     // USGS ID
        $this->assertStringContainsString('CBTT1', $html);                        // CBTT ID
        $this->assertStringContainsString('GEOS1', $html);                        // GEOS ID
        $this->assertStringContainsString('NWS1', $html);                         // NWS ID
        $this->assertStringContainsString('SNO1', $html);                         // SNOTEL ID
        // NWSLI ID → NWRFC flowplot anchor (the raw-echo branch, line 584).
        $this->assertStringContainsString('flowplot.cgi?lid=MEHO3', $html);
        // Coordinates → google maps anchor (raw-echo branch).
        $this->assertStringContainsString('google.com/maps?q=44.740000,-122.150000', $html);
        $this->assertStringContainsString('1,200 ft', $html);                     // Elevation
        $this->assertStringContainsString('250 sq mi', $html);                    // Drainage Area
        $this->assertStringContainsString('6.50', $html);                         // Bank Full
        $this->assertStringContainsString('9.00', $html);                         // Flood Stage

        // Associated Sources — counts, latest date, links.
        $this->assertStringContainsString('Associated Sources', $html);
        $this->assertStringContainsString('/source.php?id=', $html);
        $this->assertStringContainsString('full_obs_src', $html);
        $this->assertStringContainsString('full_empty_src', $html);

        // Associated Reaches — the three status classifications.
        $this->assertStringContainsString('Associated Reaches', $html);
        $this->assertStringContainsString('assoc-reaches', $html);
        $this->assertStringContainsString('<span class="level-okay">okay</span>', $html);
        $this->assertStringContainsString('<span class="level-low">low</span>', $html);
        // The no-bounds reach classifies as unknown → the muted span (line 810
        // is the non-unknown path; the unknown path is the other branch).
        $this->assertStringContainsString('unknown', $html);
        $this->assertStringContainsString('8.0 mi', $html);                       // okay reach length
        // display_name '' reach → name fallback to reach.name.
        $this->assertStringContainsString('Unknown Reach', $html);

        // Regression section — BOTH framings (target + predictor) in one slug.
        $this->assertStringContainsString('Regression analysis', $html);
        $this->assertStringContainsString('Estimated from USGS 14999999, USGS 15000000', $html);  // target intro
        $this->assertStringContainsString('Used as a predictor for', $html);                       // predictor intro
        $this->assertStringContainsString('/gauge.php?id=' . self::$predId . '"', $html);          // predictor link
        $this->assertStringContainsString('regression-facts', $html);
        $this->assertStringContainsString('<dt>intercept</dt>', $html);
        $this->assertStringContainsString('12.3456', $html);                      // large coef → number_format
        $this->assertStringContainsString('0.0034', $html);                       // small coef → %.6g
        $this->assertStringContainsString('<dt>r²</dt>', $html);
        $this->assertStringContainsString('0.9876', $html);                       // r2
        $this->assertStringContainsString('42.0 cfs', $html);                     // RMSE
        $this->assertStringContainsString('2020-01-01..2024-12-31', $html);       // window suffix
        $this->assertStringContainsString('Full analysis', $html);

        // Footer: map present → leaflet + feature-map scripts.
        $this->assertStringContainsString('/static/leaflet.js', $html);
        $this->assertStringContainsString('/static/feature-map.js', $html);
        // No editor feature (default Config) → no Edit button.
        $this->assertStringNotContainsString('/edit.php?id=', $html);
        $this->assertStringContainsString('All gauges', $html);
    }

    public function testFullGaugeHasPrevAndNext(): void
    {
        // The full gauge sits between the predictor gauge (lower id) and the
        // bare/stale gauges (higher ids), so BOTH Prev and Next are anchors.
        $html = $this->capture(fn() => handle_gauge_detail($this->pdo(), self::$fullId, null, null));
        $this->assertStringContainsString('&laquo; Prev</a>', $html);
        $this->assertStringContainsString('Next &raquo;</a>', $html);
        // Predictor gauge is id 1 → full gauge is "Gauge 2 of 6".
        $this->assertStringContainsString('Gauge 2 of 6', $html);
    }

    public function testFirstGaugeHasNoPrevAndNameFallback(): void
    {
        // The predictor gauge has the lowest id → Prev is the grey span. It has
        // no display_name → the header/h2/nav use the bare gauge name.
        $html = $this->capture(fn() => handle_gauge_detail($this->pdo(), self::$predId, null, null));
        $this->assertStringContainsString('<span style="color:#999">&laquo; Prev</span>', $html);
        $this->assertStringContainsString('Next &raquo;</a>', $html);
        $this->assertStringContainsString('Gauge 1 of 6', $html);
        $this->assertStringContainsString('<h2>PRED_GAUGE</h2>', $html);
        // Its reach reading (5000 flow) exceeds the [800,3000] band → 'high'.
        $this->assertStringContainsString('<span class="level-high">high</span>', $html);
        // The gauge-typed and mixed-typed reaches both classify 'unknown'
        // (flow reading vs gauge-typed bounds → the data_type-mismatch
        // continue branches), so the muted "unknown" badge renders.
        $this->assertStringContainsString('Gauge-typed Reach', $html);
        $this->assertStringContainsString('Mixed-typed Reach', $html);
        $this->assertStringContainsString('<span style="color:var(--c-text-muted)">unknown</span>', $html);
        // Predictor framing only (it's not a regression target itself, but its
        // calc references FULL_GAUGE, so it is itself a target via its own calc;
        // assert the section renders).
        $this->assertStringContainsString('Regression analysis', $html);
    }

    public function testRegressionEdgeBranches(): void
    {
        // The regression-edge gauge wires up the defensive slug branches:
        //   regedge_ok    → target with NO predictors + 1-entry window →
        //                   the 'another gauge' framing, no window suffix.
        //   regedge_pred  → predictor-only slug (not among this gauge's own
        //                   sources) → the "register a fresh by_slug entry".
        //   bad.slug      → fails the slug charset guard → skipped.
        //   regedge_nofiles → valid slug, no artifact files → skipped.
        //   regedge_bad   → valid + files present but JSON has no `coefs` → skipped.
        $html = $this->capture(fn() => handle_gauge_detail($this->pdo(), self::$regEdgeId, null, null));

        // Target with no predictors → the generic "another gauge" phrasing.
        $this->assertStringContainsString('Estimated from another gauge', $html);
        // Predictor-only slug rendered → "Used as a predictor for" with the
        // host gauge link.
        $this->assertStringContainsString('Used as a predictor for', $html);
        $this->assertStringContainsString('RegEdge Host', $html);
        // The invalid/no-file/no-coefs slugs emit nothing extra — assert their
        // (would-be) artifact markers are absent.
        $this->assertStringNotContainsString('bad.slug', $html);
        $this->assertStringNotContainsString('regedge_nofiles.svg', $html);
        $this->assertStringNotContainsString('regedge_bad.svg', $html);
    }

    public function testLastGaugeHasNoNext(): void
    {
        // The regression-host gauge has the highest id → Next is the grey span.
        $html = $this->capture(fn() => handle_gauge_detail($this->pdo(), self::$lastId, null, null));
        $this->assertStringContainsString('&laquo; Prev</a>', $html);
        $this->assertStringContainsString('<span style="color:#999">Next &raquo;</span>', $html);
        $this->assertStringContainsString('Gauge 6 of 6', $html);
    }

    public function testBareGaugeEmptyBranches(): void
    {
        // No readings, no coords, no sources, no reaches.
        $html = $this->capture(fn() => handle_gauge_detail($this->pdo(), self::$bareId, null, null));
        // Empty-readings banner (red) + no readings table / date form / plots.
        $this->assertStringContainsString('No cached observations for this gauge.', $html);
        $this->assertStringNotContainsString('class="readings-table"', $html);
        $this->assertStringNotContainsString('name="start"', $html);
        // No coords + no reach geom → no map, no leaflet scripts.
        $this->assertStringNotContainsString('id="feature-map"', $html);
        $this->assertStringNotContainsString('/static/leaflet.js', $html);
        // Empty associated sub-tables.
        $this->assertStringContainsString('No associated sources.', $html);
        $this->assertStringContainsString('No associated reaches.', $html);
        // Bare gauge name fallback (no display_name).
        $this->assertStringContainsString('<h2>BARE_GAUGE</h2>', $html);
        // No regression artifacts for this gauge → section absent.
        $this->assertStringNotContainsString('Regression analysis', $html);
    }

    public function testStaleGaugeBannerAndTrackOnlyMap(): void
    {
        // Latest reading is 30 days old → the yellow stale banner.
        $html = $this->capture(fn() => handle_gauge_detail($this->pdo(), self::$staleId, null, null));
        $this->assertStringContainsString('days ago', $html);
        // The gauge has NO coords but its reach has a LineString geom → the map
        // renders from the reach track alone (the no-gauge-coords branch).
        $this->assertStringContainsString('id="feature-map"', $html);
        $this->assertStringContainsString('data-reach-tracks=', $html);
        // Falling reading (delta -0.8) → falling status in the readings table.
        $this->assertStringContainsString('<span class="falling">falling</span>', $html);
    }

    public function testExplicitDateWindowRendersPlots(): void
    {
        // Explicit start/end → the non-default window branch of
        // gp_resolve_window (date_ts) + the date-form value echo.
        $end = gmdate('Y-m-d');
        $start = gmdate('Y-m-d', time() - 5 * 86400);
        $html = $this->capture(fn() => handle_gauge_detail($this->pdo(), self::$fullId, $start, $end));
        $this->assertStringContainsString('value="' . $start . '"', $html);
        $this->assertStringContainsString('value="' . $end . '"', $html);
        $this->assertStringContainsString('plot-container', $html);
        $this->assertStringContainsString('</html>', $html);
    }
}
