<?php

declare(strict_types=1);

require_once __DIR__ . '/IntegrationTestCase.php';

/**
 * Integration tests for gauge_picker.php.
 *
 * Focus: the optional ?state=<full name> query param pre-checks only that
 * state's pill — the discoverability hook from gauges.<state>.html — plus the
 * multi-state border-gauge case (gauge.state = 'OR,WA' must surface under both
 * Oregon and Washington, in the pills and the AJAX filter).
 *
 * Seed: 3 gauges — one in OR, one in MT, one OR,WA border gauge (the Columbia
 * mainstem shape). State pills are emitted only for states that have at least
 * one gauge with current observations, so each seed needs a
 * latest_gauge_observation row.
 */
final class GaugePickerIntegrationTest extends IntegrationTestCase
{
    protected static function seedDatabase(PDO $db): void
    {
        $db->exec(
            "INSERT INTO gauge (id, name, display_name, state, huc) VALUES
             (8001, 'PICKER_OR',   'Picker Oregon', 'OR',    '17090011'),
             (8002, 'PICKER_MT',   'Picker Montana','MT',    '17010205'),
             (8003, 'PICKER_ORWA', 'Picker Border', 'OR,WA', '17080003')"
        );
        // Each needs a latest_gauge_observation row so the picker's
        // "states with current data" query picks it up.
        $db->exec(
            "INSERT INTO latest_gauge_observation (gauge_id, data_type, value, observed_at) VALUES
             (8001, 'flow', 1000.0,   datetime('now', '-1 hour')),
             (8002, 'flow', 500.0,    datetime('now', '-1 hour')),
             (8003, 'flow', 250000.0, datetime('now', '-1 hour'))"
        );
    }

    /** Find the AJAX row with the given gauge id, or null. */
    private static function rowById(mixed $rows, int $id): ?array
    {
        if (!is_array($rows)) {
            return null;
        }
        foreach ($rows as $row) {
            if (is_array($row) && (int)($row['id'] ?? 0) === $id) {
                return $row;
            }
        }
        return null;
    }

    public function testNoStateParamChecksAllPills(): void
    {
        $resp = $this->request('/gauge_picker.php');

        $this->assertSame(200, $resp['status']);
        // Both seeded states get a pill with `checked` — back-compat.
        $this->assertMatchesRegularExpression(
            '/value="Oregon"\s+checked/',
            $resp['body'],
            'Oregon pill should be checked by default'
        );
        $this->assertMatchesRegularExpression(
            '/value="Montana"\s+checked/',
            $resp['body'],
            'Montana pill should be checked by default'
        );
    }

    public function testStateParamPreChecksOnlyThatPill(): void
    {
        $resp = $this->request('/gauge_picker.php', ['state' => 'Montana']);

        $this->assertSame(200, $resp['status']);
        // Montana pill checked
        $this->assertMatchesRegularExpression(
            '/value="Montana"\s+checked/',
            $resp['body'],
            'Montana pill should be checked when ?state=Montana'
        );
        // Oregon pill NOT checked — pre-init focuses on a single state
        $this->assertMatchesRegularExpression(
            '/value="Oregon"\s*>/',
            $resp['body'],
            'Oregon pill should NOT be checked when ?state=Montana'
        );
        $this->assertDoesNotMatchRegularExpression(
            '/value="Oregon"\s+checked/',
            $resp['body']
        );
    }

    public function testInvalidStateParamFallsBackToAllChecked(): void
    {
        // ?state=Nowhere doesn't match any seeded state → behave like no param.
        $resp = $this->request('/gauge_picker.php', ['state' => 'Nowhere']);

        $this->assertSame(200, $resp['status']);
        $this->assertMatchesRegularExpression(
            '/value="Oregon"\s+checked/',
            $resp['body']
        );
        $this->assertMatchesRegularExpression(
            '/value="Montana"\s+checked/',
            $resp['body']
        );
    }

    public function testEmptyStateParamFallsBackToAllChecked(): void
    {
        $resp = $this->request('/gauge_picker.php', ['state' => '']);

        $this->assertSame(200, $resp['status']);
        $this->assertMatchesRegularExpression(
            '/value="Oregon"\s+checked/',
            $resp['body']
        );
        $this->assertMatchesRegularExpression(
            '/value="Montana"\s+checked/',
            $resp['body']
        );
    }

    public function testBorderGaugeSurfacesWashingtonPill(): void
    {
        // No single-state WA gauge is seeded; the Washington pill must come
        // from splitting the OR,WA border gauge's state on the comma. Before
        // the fix, 'OR,WA' mapped to no pill at all.
        $resp = $this->request('/gauge_picker.php');

        $this->assertSame(200, $resp['status']);
        $this->assertStringContainsString(
            'value="Washington"',
            $resp['body'],
            'OR,WA border gauge should contribute a Washington pill'
        );
        $this->assertStringContainsString('value="Oregon"', $resp['body']);
    }

    public function testAjaxBorderGaugeMatchesWashingtonAlone(): void
    {
        // A border gauge stored as 'OR,WA' must match a Washington-only
        // filter — `g.state IN ('WA')` never would. Its returned state is the
        // re-joined full names the client splits to bucket it under each pill.
        $resp = $this->request('/gauge_picker.php', ['ajax' => '1', 'states' => 'Washington']);

        $this->assertSame(200, $resp['status']);
        $rows = json_decode($resp['body'], true);
        $row = self::rowById($rows, 8003);
        $this->assertNotNull($row, 'OR,WA gauge should match a Washington-only filter');
        $this->assertSame('Oregon,Washington', $row['state'] ?? null);
    }

    public function testAjaxBorderGaugeMatchesOregonAlongsideSingleState(): void
    {
        // Filtering by Oregon returns both the single-state OR gauge and the
        // OR,WA border gauge.
        $resp = $this->request('/gauge_picker.php', ['ajax' => '1', 'states' => 'Oregon']);

        $this->assertSame(200, $resp['status']);
        $rows = json_decode($resp['body'], true);
        $this->assertNotNull(self::rowById($rows, 8001), 'single-state OR gauge still matches');
        $this->assertNotNull(self::rowById($rows, 8003), 'OR,WA border gauge also matches Oregon');
    }

    public function testAjaxSingleStateGaugeNotMatchedByForeignState(): void
    {
        // Guard against an over-broad match: the Montana gauge must NOT leak
        // into a Washington filter (the comma-wrapped INSTR is anchored).
        $resp = $this->request('/gauge_picker.php', ['ajax' => '1', 'states' => 'Washington']);

        $this->assertSame(200, $resp['status']);
        $rows = json_decode($resp['body'], true);
        $this->assertNull(self::rowById($rows, 8002), 'MT gauge must not match a Washington filter');
    }
}
