<?php

declare(strict_types=1);

require_once __DIR__ . '/FunctionalTestCase.php';
require_once __DIR__ . '/Fixtures.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/db.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/header.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/footer.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/custom_gauges_handler.php';

/**
 * In-process functional coverage for custom_gauges_handler.php
 * (handle_custom_gauges + its private loaders/renderers).
 *
 * Drives the gauge-row LEFT JOIN, the per-gauge status rollup
 * (low/okay/high/null with the tie-break), the URL-order reorder, the
 * state + nested HUC6/HUC8 watershed filter (with huc_name lookups and
 * the "(no HUC)" branch), and the 8-column table's flow/inflow/gage
 * fallback, gage feet, temp cells, and dataset-backed state labels.
 */
final class CustomGaugesHandlerFunctionalTest extends FunctionalTestCase
{
    /** OR gauge, huc 17090010, flow 800 in [500,2000] → rollup 'okay' */
    private static int $gaugeOkay = 0;
    /** WA gauge, huc 17110005, flow 100 below range → rollup 'low' */
    private static int $gaugeLow = 0;
    /** OR gauge, same huc6 as okay, flow 5000 above range → rollup 'high' */
    private static int $gaugeHigh = 0;
    /** gauge with no state/huc and inflow-only reading → "(no HUC)" + inflow fallback */
    private static int $gaugeNoHuc = 0;
    /** gauge with only a gage reading (no flow/inflow) → feet fallback in flow cell */
    private static int $gaugeGageOnly = 0;
    /** gauge with a reach but no class range → rollup label NULL (line 169) */
    private static int $gaugeNullRollup = 0;
    /** gauge with no readings at all → blank flow cell (else branch, line 433) */
    private static int $gaugeBlank = 0;
    /** gauge in a synthetic dataset state that was never in the PHP hardcoded map */
    private static int $gaugeDatasetState = 0;

    protected static function seedDatabase(PDO $db): void
    {
        $db->exec("INSERT INTO state (name, abbreviation) VALUES ('Atlantis', 'ZZ')");

        // HUC name lookups for the nested watershed filter.
        Fixtures::hucName($db, '17090010', 8, 'Middle Willamette');
        Fixtures::hucName($db, '17110005', 8, 'Puget Sound');
        Fixtures::hucName($db, '17090011', 8, 'Yamhill');
        Fixtures::hucName($db, '170900', 6, 'Willamette');
        Fixtures::hucName($db, '171100', 6, 'Puget');

        // --- okay gauge: flow 800 inside [500, 2000] via its reach class ---
        self::$gaugeOkay = Fixtures::gauge($db, [
            'river' => 'Sandy', 'location' => 'Marmot', 'state' => 'OR', 'huc' => '17090010',
        ]);
        Fixtures::latestGaugeObservation($db, self::$gaugeOkay, [
            'data_type' => 'flow', 'value' => 800.0,
        ]);
        Fixtures::latestGaugeObservation($db, self::$gaugeOkay, [
            'data_type' => 'gauge', 'value' => 3.5,
        ]);
        Fixtures::latestGaugeObservation($db, self::$gaugeOkay, [
            'data_type' => 'temperature', 'value' => 48.0,
        ]);
        $rOkay = Fixtures::reach($db, ['name' => 'Okay Run', 'gauge_id' => self::$gaugeOkay]);
        Fixtures::reachClass($db, $rOkay, [
            'name' => 'III', 'low' => 500.0, 'high' => 2000.0,
            'low_data_type' => 'flow', 'high_data_type' => 'flow',
        ]);

        // --- low gauge: flow 100 below the range; different huc6 ---
        self::$gaugeLow = Fixtures::gauge($db, [
            'river' => 'Skykomish', 'location' => 'Gold Bar', 'state' => 'WA', 'huc' => '17110005',
        ]);
        Fixtures::latestGaugeObservation($db, self::$gaugeLow, [
            'data_type' => 'flow', 'value' => 100.0,
        ]);
        $rLow = Fixtures::reach($db, ['name' => 'Low Run', 'gauge_id' => self::$gaugeLow]);
        Fixtures::reachClass($db, $rLow, [
            'name' => 'IV', 'low' => 500.0, 'high' => 2000.0,
            'low_data_type' => 'flow', 'high_data_type' => 'flow',
        ]);

        // --- high gauge: flow 5000 above the range; huc6 shared with okay
        //     (17090011 → 170900) so a huc6 group has two huc8 children.
        self::$gaugeHigh = Fixtures::gauge($db, [
            'river' => 'Clackamas', 'location' => 'Estacada', 'state' => 'OR', 'huc' => '17090011',
        ]);
        Fixtures::latestGaugeObservation($db, self::$gaugeHigh, [
            'data_type' => 'flow', 'value' => 5000.0,
        ]);
        // One reach, flow above range → n_high=1, n_low=0, n_okay=0 ⇒ the
        // rollup ternary takes its 'high' (false) branch. ($gaugeLow covers
        // the 'low' / true branch; $gaugeOkay covers the okay-wins branch.)
        $rHi1 = Fixtures::reach($db, ['name' => 'High Run A', 'gauge_id' => self::$gaugeHigh]);
        Fixtures::reachClass($db, $rHi1, [
            'name' => 'V', 'low' => 500.0, 'high' => 2000.0,
            'low_data_type' => 'flow', 'high_data_type' => 'flow',
        ]);

        // --- no-HUC gauge: no state/huc; inflow-only (flow NULL) reading ---
        self::$gaugeNoHuc = Fixtures::gauge($db, ['name' => 'NOHUC_GAUGE']);
        Fixtures::latestGaugeObservation($db, self::$gaugeNoHuc, [
            'data_type' => 'inflow', 'value' => 1234.0,
        ]);

        // --- gage-only gauge: only a 'gauge' reading → feet fallback ---
        self::$gaugeGageOnly = Fixtures::gauge($db, ['river' => 'Rogue', 'location' => 'Agness']);
        Fixtures::latestGaugeObservation($db, self::$gaugeGageOnly, [
            'data_type' => 'gauge', 'value' => 6.7,
        ]);

        // --- null-rollup gauge: a reach with flow but no class range, so the
        //     rollup CASE yields NULL for every reach → label null branch.
        self::$gaugeNullRollup = Fixtures::gauge($db, ['river' => 'McKenzie', 'location' => 'Vida']);
        Fixtures::latestGaugeObservation($db, self::$gaugeNullRollup, [
            'data_type' => 'flow', 'value' => 900.0,
        ]);
        Fixtures::reach($db, ['name' => 'No-Range Run', 'gauge_id' => self::$gaugeNullRollup]);

        // --- blank gauge: no readings of any kind → empty flow/gage/temp cells.
        self::$gaugeBlank = Fixtures::gauge($db, ['river' => 'Deschutes', 'location' => 'Maupin']);

        // --- synthetic dataset state: proves state labels come from the DB, not
        //     an engine-owned allowlist of the current WKCC presentation states.
        self::$gaugeDatasetState = Fixtures::gauge($db, [
            'river' => 'Mystery', 'location' => 'Lagoon', 'state' => 'ZZ', 'huc' => '17090010',
        ]);
        Fixtures::latestGaugeObservation($db, self::$gaugeDatasetState, [
            'data_type' => 'flow', 'value' => 42.0,
        ]);
    }

    public function testRendersStatusRollupLabels(): void
    {
        $ids = [self::$gaugeOkay, self::$gaugeLow, self::$gaugeHigh];
        $html = $this->capture(fn() => handle_custom_gauges($this->pdo(), $ids, implode(',', array_map('pubhash_encode', $ids))));

        $this->assertStringContainsString('<table class="levels">', $html);
        $this->assertStringContainsString('Sandy', $html);
        $this->assertStringContainsString('Skykomish', $html);
        $this->assertStringContainsString('Clackamas', $html);

        // Rollup labels: okay (800 in range), low (100 below), high (5000 above).
        $this->assertStringContainsString('level-okay', $html);
        $this->assertStringContainsString('level-low', $html);
        $this->assertStringContainsString('level-high', $html);

        // data-status attrs on the rows.
        $this->assertStringContainsString('data-status="okay"', $html);

        // "3 gauges" count (plural).
        $this->assertStringContainsString('3 gauge', $html);
    }

    public function testStateAndNestedWatershedFilters(): void
    {
        // OR (okay+high) + WA (low) → State group renders (count > 1).
        $ids = [self::$gaugeOkay, self::$gaugeLow, self::$gaugeHigh];
        $html = $this->capture(fn() => handle_custom_gauges($this->pdo(), $ids, implode(',', array_map('pubhash_encode', $ids))));

        // State filter: abbrevs map to display names via the state table.
        $this->assertStringContainsString('data-group="state"', $html);
        $this->assertStringContainsString('value="Oregon"', $html);
        $this->assertStringContainsString('value="Washington"', $html);

        // Watershed (huc8) filter with nested huc6 parents.
        $this->assertStringContainsString('data-group="huc8"', $html);
        $this->assertStringContainsString('data-huc6="170900"', $html);
        $this->assertStringContainsString('data-huc6="171100"', $html);
        // HUC8 leaf names resolved from huc_name.
        $this->assertStringContainsString('Middle Willamette', $html);
        $this->assertStringContainsString('Yamhill', $html);
        // HUC6 parent name resolved from huc_name.
        $this->assertStringContainsString('Willamette', $html);
    }

    public function testStateLabelsComeFromStateTable(): void
    {
        $ids = [self::$gaugeOkay, self::$gaugeDatasetState];
        $html = $this->capture(fn() => handle_custom_gauges($this->pdo(), $ids, implode(',', array_map('pubhash_encode', $ids))));

        $this->assertStringContainsString('Mystery', $html);
        $this->assertStringContainsString('value="Atlantis"', $html);
        $this->assertStringContainsString('data-state="Atlantis"', $html);
    }

    public function testNoHucBranchAndInflowFallback(): void
    {
        // Single OR-less gauge with no huc → "(no HUC)" row; inflow-only
        // reading flows into the Flow cell via the flow ?? inflow fallback.
        $html = $this->capture(
            fn() => handle_custom_gauges($this->pdo(), [self::$gaugeNoHuc], pubhash_encode(self::$gaugeNoHuc))
        );

        $this->assertStringContainsString('(no HUC)', $html);
        // inflow 1234 → ">1,234<" in the flow cell.
        $this->assertStringContainsString('>1,234<', $html);
        // No status badge (no reaches → no rollup).
        $this->assertStringNotContainsString('level-okay', $html);
        // "1 gauge" singular.
        $this->assertStringContainsString('1 gauge<', $html);
    }

    public function testGageOnlyFallsBackToFeet(): void
    {
        $html = $this->capture(
            fn() => handle_custom_gauges($this->pdo(), [self::$gaugeGageOnly], pubhash_encode(self::$gaugeGageOnly))
        );

        // No flow/inflow → flow cell shows gage as feet: 6.7 → "6.7&prime;".
        $this->assertStringContainsString('&prime;', $html);
        // Gage cell at 1 decimal.
        $this->assertStringContainsString('>6.7<', $html);
    }

    public function testReadingsAndTimeRender(): void
    {
        $html = $this->capture(
            fn() => handle_custom_gauges($this->pdo(), [self::$gaugeOkay], pubhash_encode(self::$gaugeOkay))
        );

        // Flow int, gage 1dp, temp 1dp.
        $this->assertStringContainsString('>800<', $html);
        $this->assertStringContainsString('>3.5<', $html);
        $this->assertStringContainsString('>48.0<', $html);
        // Best-available <time> from the four timestamps.
        $this->assertStringContainsString('<time datetime="', $html);
        // Status tooltip with bucket count summary on the okay rollup.
        $this->assertStringContainsString('title="', $html);
    }

    public function testReorderByUrlPosition(): void
    {
        // Request high before okay — even though okay has the lower id.
        $ids = [self::$gaugeHigh, self::$gaugeOkay];
        $html = $this->capture(fn() => handle_custom_gauges($this->pdo(), $ids, implode(',', array_map('pubhash_encode', $ids))));

        $posHigh = strpos($html, 'Clackamas');
        $posOkay = strpos($html, 'Sandy');
        $this->assertNotFalse($posHigh);
        $this->assertNotFalse($posOkay);
        $this->assertLessThan($posOkay, $posHigh, 'URL order (high before okay) not preserved');
    }

    public function testNullRollupAndBlankFlowCell(): void
    {
        // null-rollup: reach present but no class range → no status badge even
        // though the gauge has flow. blank: no readings → empty flow cell.
        $ids = [self::$gaugeNullRollup, self::$gaugeBlank];
        $html = $this->capture(fn() => handle_custom_gauges($this->pdo(), $ids, implode(',', array_map('pubhash_encode', $ids))));

        $this->assertStringContainsString('McKenzie', $html);
        $this->assertStringContainsString('Deschutes', $html);
        // No status pill for either: null-rollup yields a null label; blank has
        // no reaches at all.
        $this->assertStringNotContainsString('level-okay', $html);
        $this->assertStringNotContainsString('level-low', $html);
        $this->assertStringNotContainsString('level-high', $html);
        // The null-rollup gauge's flow still renders (900); the blank gauge's
        // flow/gage/temp cells are all empty (else branch in the flow cell).
        $this->assertStringContainsString('>900<', $html);
    }

    public function testEmptyIdsRendersEmptyTable(): void
    {
        // Handler must survive an empty id list (the shim redirects first).
        $html = $this->capture(fn() => handle_custom_gauges($this->pdo(), [], ''));

        $this->assertStringContainsString('<table class="levels">', $html);
        $this->assertStringContainsString('0 gauge', $html);
        // No state group, no watershed group (nothing present).
        $this->assertStringNotContainsString('data-group="state"', $html);
    }
}
