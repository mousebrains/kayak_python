<?php

declare(strict_types=1);

require_once __DIR__ . '/IntegrationTestCase.php';

/**
 * Smoke test for /status.json (PLAN_production_discipline.md Phase 2.1).
 *
 * Asserts the endpoint returns 200 with valid JSON carrying the agreed
 * top-level keys. Field-level math (per-agency counts, per-status
 * counts) isn't covered — the test DB seeded by `levels init-db` has
 * no observations or gauge state, so the response is mostly zeros.
 * Manual curl + in-browser checks against the live endpoint cover the
 * rendered values; the CORS allow-list (now sourced from
 * HostConfig.allowed_origins via runtime-config.json) is covered below.
 */
class StatusIntegrationTest extends IntegrationTestCase
{
    protected static function seedDatabase(PDO $db): void
    {
        // No extra seed — the smoke test only cares about response shape.
    }

    public function testStatusJsonReturnsExpectedShape(): void
    {
        $resp = $this->request('/status.php');

        $this->assertSame(200, $resp['status']);
        $this->assertStringContainsString(
            'application/json',
            $resp['headers']['content-type'] ?? '',
        );
        $this->assertStringContainsString(
            'no-cache',
            $resp['headers']['cache-control'] ?? '',
        );

        $decoded = json_decode($resp['body'], true);
        $this->assertIsArray($decoded, 'response body should be JSON');
        $this->assertArrayHasKey('build_at', $decoded);
        $this->assertArrayHasKey('latest_observation_at', $decoded);
        $this->assertArrayHasKey('stale_threshold_hours', $decoded);
        $this->assertArrayHasKey('sources_by_agency', $decoded);
        $this->assertArrayHasKey('gauges_by_status', $decoded);
        $this->assertArrayHasKey('totals', $decoded);

        $this->assertSame(48, $decoded['stale_threshold_hours']);
        $this->assertIsArray($decoded['sources_by_agency']);
        $this->assertIsArray($decoded['gauges_by_status']);
        $this->assertIsArray($decoded['totals']);
    }

    public function testCorsEchoesAnAllowedOrigin(): void
    {
        // The test's runtime-config.json comes from `levels emit-config`, which
        // bridges HostConfig.allowed_origins; with no host.yaml that's the engine
        // default list, which includes the canonical site origin.
        $allowed = 'https://levels.wkcc.org';
        $resp = $this->request('/status.php', headers: ['Origin' => $allowed]);

        $this->assertSame(200, $resp['status']);
        $this->assertSame($allowed, $resp['headers']['access-control-allow-origin'] ?? null);
        $this->assertStringContainsString('Origin', $resp['headers']['vary'] ?? '');
    }

    public function testCorsDeniesAnUnlistedOrigin(): void
    {
        $resp = $this->request('/status.php', headers: ['Origin' => 'https://evil.example']);

        $this->assertSame(200, $resp['status']);
        // No Access-Control-Allow-Origin header for an origin not in the list.
        $this->assertArrayNotHasKey('access-control-allow-origin', $resp['headers']);
        // But Vary: Origin is still set (even on deny) so a shared cache can't
        // serve this no-CORS response to a later allowed origin.
        $this->assertStringContainsString('Origin', $resp['headers']['vary'] ?? '');
    }
}
