<?php

declare(strict_types=1);

require_once __DIR__ . '/IntegrationTestCase.php';

/**
 * Integration tests for picker.php (reach picker).
 *
 * Focus: the optional ?state=<full name> query param overrides the
 * data-derived primary state. Mirrors GaugePickerIntegrationTest for the
 * parallel gauge picker.
 *
 * Seed: 3 reaches in two states. Both states need to appear in the
 * `state` table and have at least one reach with no_show = 0 so picker.php's
 * "states with reaches" query picks them up.
 */
final class PickerIntegrationTest extends IntegrationTestCase
{
    protected static function seedDatabase(PDO $db): void
    {
        // Reaches — one in Oregon, two in Washington. The no-param fallback
        // should choose Washington from the data, not a hardcoded region.
        $db->exec(
            "INSERT INTO reach (id, name, display_name, sort_name, no_show) VALUES
             (9001, 'PICK_OR', 'Picker Oregon Reach', 'oregon|0|aaaa', 0),
             (9002, 'PICK_WA', 'Picker Washington Reach', 'washington|0|aaaa', 0),
             (9003, 'PICK_WA_2', 'Picker Washington Reach 2', 'washington|0|aaab', 0)"
        );
        $orId = (int)$db->query("SELECT id FROM state WHERE abbreviation = 'OR'")->fetchColumn();
        $waId = (int)$db->query("SELECT id FROM state WHERE abbreviation = 'WA'")->fetchColumn();
        $db->prepare('INSERT INTO reach_state (reach_id, state_id) VALUES (?, ?)')
            ->execute([9001, $orId]);
        $db->prepare('INSERT INTO reach_state (reach_id, state_id) VALUES (?, ?)')
            ->execute([9002, $waId]);
        $db->prepare('INSERT INTO reach_state (reach_id, state_id) VALUES (?, ?)')
            ->execute([9003, $waId]);
    }

    public function testNoStateParamDefaultsToMostRepresentedState(): void
    {
        $resp = $this->request('/picker.php');

        $this->assertSame(200, $resp['status']);
        $this->assertMatchesRegularExpression(
            '/value="Washington"\s+checked/',
            $resp['body'],
            'Washington pill should be checked because it has the most reaches'
        );
        $this->assertDoesNotMatchRegularExpression(
            '/value="Oregon"\s+checked/',
            $resp['body'],
            'Oregon should not be the implicit default when another state has more reaches'
        );
    }

    public function testStateParamOverridesPrimary(): void
    {
        $resp = $this->request('/picker.php', ['state' => 'Oregon']);

        $this->assertSame(200, $resp['status']);
        $this->assertMatchesRegularExpression(
            '/value="Oregon"\s+checked/',
            $resp['body'],
            'Oregon pill should be checked when ?state=Oregon'
        );
        $this->assertDoesNotMatchRegularExpression(
            '/value="Washington"\s+checked/',
            $resp['body'],
            'Washington pill should NOT be checked when ?state=Oregon'
        );
    }

    public function testInvalidStateParamFallsBackToMostRepresentedState(): void
    {
        // ?state=Nowhere doesn't match any seeded state → fall back to the
        // data-derived primary state.
        $resp = $this->request('/picker.php', ['state' => 'Nowhere']);

        $this->assertSame(200, $resp['status']);
        $this->assertMatchesRegularExpression(
            '/value="Washington"\s+checked/',
            $resp['body']
        );
        $this->assertDoesNotMatchRegularExpression(
            '/value="Oregon"\s+checked/',
            $resp['body']
        );
    }
}
