<?php

declare(strict_types=1);

require_once __DIR__ . '/FunctionalTestCase.php';
require_once __DIR__ . '/../../php/includes/db.php';
require_once __DIR__ . '/../../php/includes/header.php';
require_once __DIR__ . '/../../php/includes/footer.php';
require_once __DIR__ . '/../../php/includes/gauge_detail.php';

/**
 * Proof-of-harness functional test: drives handle_gauge_detail() in-process so
 * pcov counts gauge_detail.php (the subprocess integration tests can't), and
 * exercises both the render path and the 404 early-out via the http_terminate
 * seam.
 */
final class GaugeDetailFunctionalTest extends FunctionalTestCase
{
    protected static function seedDatabase(PDO $db): void
    {
        $db->exec(
            "INSERT INTO gauge (name, river, location, state) "
            . "VALUES ('Test Gauge', 'Test River', 'at Testville', 'OR')"
        );
        $gid = (int) $db->lastInsertId();
        $db->prepare(
            "INSERT INTO latest_gauge_observation "
            . "(gauge_id, data_type, observed_at, value, delta_per_hour) "
            . "VALUES (?, 'flow', datetime('now'), 250.0, 1.5)"
        )->execute([$gid]);
        $db->prepare(
            "INSERT INTO reach (name, sort_name, gauge_id) VALUES ('Test Reach', 'test reach', ?)"
        )->execute([$gid]);
    }

    private function gaugeId(): int
    {
        return (int) $this->pdo()->query("SELECT id FROM gauge WHERE name = 'Test Gauge'")->fetchColumn();
    }

    public function testRendersGaugePage(): void
    {
        $html = $this->capture(fn() => handle_gauge_detail($this->pdo(), $this->gaugeId(), null, null));
        $this->assertStringContainsString('Test River', $html);
        $this->assertStringContainsString('Test Reach', $html);
    }

    public function testUnknownGaugeIs404(): void
    {
        $e = $this->captureExit(fn() => handle_gauge_detail($this->pdo(), 999999, null, null));
        $this->assertSame(404, $e->statusCode);
    }
}
