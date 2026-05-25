<?php

declare(strict_types=1);

require_once __DIR__ . '/FunctionalTestCase.php';
require_once __DIR__ . '/Fixtures.php';
require_once __DIR__ . '/../../php/includes/db.php';
require_once __DIR__ . '/../../php/includes/header.php';
require_once __DIR__ . '/../../php/includes/footer.php';
require_once __DIR__ . '/../../php/includes/gauge_detail.php';

/**
 * Proof-of-harness functional test: drives handle_gauge_detail() in-process so
 * pcov counts gauge_detail.php (the subprocess integration tests can't), and
 * exercises both the render path and the 404 early-out via the http_terminate
 * seam. Doubles as a smoke test of the Fixtures factory layer.
 */
final class GaugeDetailFunctionalTest extends FunctionalTestCase
{
    private static int $gaugeId = 0;

    protected static function seedDatabase(PDO $db): void
    {
        self::$gaugeId = Fixtures::gauge($db, [
            'river' => 'Test River',
            'location' => 'at Testville',
            'state' => 'OR',
        ]);
        Fixtures::latestGaugeObservation($db, self::$gaugeId, ['value' => 250.0, 'delta_per_hour' => 1.5]);
        Fixtures::reach($db, ['name' => 'Test Reach', 'gauge_id' => self::$gaugeId]);
    }

    public function testRendersGaugePage(): void
    {
        $html = $this->capture(fn() => handle_gauge_detail($this->pdo(), self::$gaugeId, null, null));
        $this->assertStringContainsString('Test River', $html);
        $this->assertStringContainsString('Test Reach', $html);
    }

    public function testUnknownGaugeIs404(): void
    {
        $e = $this->captureExit(fn() => handle_gauge_detail($this->pdo(), 999999, null, null));
        $this->assertSame(404, $e->statusCode);
    }
}
