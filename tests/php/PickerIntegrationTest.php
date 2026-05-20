<?php

declare(strict_types=1);

require_once __DIR__ . '/IntegrationTestCase.php';

/**
 * Integration tests for picker.php (reach picker).
 *
 * Focus: the optional ?state=<full name> query param overrides the
 * hardcoded $primary_state = 'Oregon' default. Mirrors GaugePickerIntegrationTest
 * for the parallel gauge picker.
 *
 * Seed: 2 reaches in different states. Both states need to appear in the
 * `state` table and have at least one reach with no_show = 0 so picker.php's
 * "states with reaches" query picks them up.
 */
final class PickerIntegrationTest extends IntegrationTestCase
{
    protected static function seedDatabase(PDO $db): void
    {
        // Reaches — one in Oregon, one in Washington.
        $db->exec(
            "INSERT INTO reach (id, name, display_name, sort_name, no_show) VALUES
             (9001, 'PICK_OR', 'Picker Oregon Reach', 'oregon|0|aaaa', 0),
             (9002, 'PICK_WA', 'Picker Washington Reach', 'washington|0|aaaa', 0)"
        );
        $orId = (int)$db->query("SELECT id FROM state WHERE abbreviation = 'OR'")->fetchColumn();
        $waId = (int)$db->query("SELECT id FROM state WHERE abbreviation = 'WA'")->fetchColumn();
        $db->prepare('INSERT INTO reach_state (reach_id, state_id) VALUES (?, ?)')
            ->execute([9001, $orId]);
        $db->prepare('INSERT INTO reach_state (reach_id, state_id) VALUES (?, ?)')
            ->execute([9002, $waId]);
    }

    public function testNoStateParamDefaultsToOregon(): void
    {
        $resp = $this->request('/picker.php');

        $this->assertSame(200, $resp['status']);
        // Oregon is the hardcoded default primary state.
        $this->assertMatchesRegularExpression(
            '/value="Oregon"\s+checked/',
            $resp['body'],
            'Oregon pill should be checked by default'
        );
        // Washington pill should NOT be checked (single-state primary).
        $this->assertDoesNotMatchRegularExpression(
            '/value="Washington"\s+checked/',
            $resp['body']
        );
    }

    public function testStateParamOverridesPrimary(): void
    {
        $resp = $this->request('/picker.php', ['state' => 'Washington']);

        $this->assertSame(200, $resp['status']);
        $this->assertMatchesRegularExpression(
            '/value="Washington"\s+checked/',
            $resp['body'],
            'Washington pill should be checked when ?state=Washington'
        );
        $this->assertDoesNotMatchRegularExpression(
            '/value="Oregon"\s+checked/',
            $resp['body'],
            'Oregon pill should NOT be checked when ?state=Washington'
        );
    }

    public function testInvalidStateParamFallsBackToOregon(): void
    {
        // ?state=Nowhere doesn't match any seeded state → fall back to 'Oregon'.
        $resp = $this->request('/picker.php', ['state' => 'Nowhere']);

        $this->assertSame(200, $resp['status']);
        $this->assertMatchesRegularExpression(
            '/value="Oregon"\s+checked/',
            $resp['body']
        );
        $this->assertDoesNotMatchRegularExpression(
            '/value="Washington"\s+checked/',
            $resp['body']
        );
    }
}
