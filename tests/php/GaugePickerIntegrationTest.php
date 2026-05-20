<?php

declare(strict_types=1);

require_once __DIR__ . '/IntegrationTestCase.php';

/**
 * Integration tests for gauge_picker.php.
 *
 * Focus: the optional ?state=<full name> query param pre-checks only that
 * state's pill — the discoverability hook from gauges.<state>.html.
 *
 * Seed: 2 gauges (one in OR, one in MT). State pills are emitted only
 * for states that have at least one gauge with current observations,
 * so both seeds need a latest_gauge_observation row.
 */
final class GaugePickerIntegrationTest extends IntegrationTestCase
{
    protected static function seedDatabase(PDO $db): void
    {
        $db->exec(
            "INSERT INTO gauge (id, name, display_name, state, huc) VALUES
             (8001, 'PICKER_OR', 'Picker Oregon', 'OR', '17090011'),
             (8002, 'PICKER_MT', 'Picker Montana', 'MT', '17010205')"
        );
        // Both need a latest_gauge_observation row so the picker's
        // "states with current data" query picks them up.
        $db->exec(
            "INSERT INTO latest_gauge_observation (gauge_id, data_type, value, observed_at) VALUES
             (8001, 'flow', 1000.0, datetime('now', '-1 hour')),
             (8002, 'flow', 500.0, datetime('now', '-1 hour'))"
        );
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
}
