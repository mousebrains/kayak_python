<?php

declare(strict_types=1);

require_once __DIR__ . '/FunctionalTestCase.php';
require_once __DIR__ . '/Fixtures.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/editor_bridge.php';

/**
 * In-process functional coverage for editor_bridge.php (Tier 2 of the
 * editor → kayak_data PR bridge): the bridgeability gate, the reviewed-base
 * capture, and the idempotent change_request_bridge enqueue. Runs against the
 * real init-db schema so change_request_bridge / reach / gauge all exist and
 * the FKs are real.
 *
 * The review.php / edit.php callers are covered end-to-end by
 * ReviewApproveRaceTest, ReviewIntegrationTest, and EditDirectIntegrationTest;
 * this class exercises the helper directly for pcov.
 */
final class EditorBridgeFunctionalTest extends FunctionalTestCase
{
    /** Seed an editor + approved change_request, return [cr_id, maint_id]. */
    private function seedCr(string $targetType, ?int $targetId): array
    {
        $db = $this->pdo();
        $maint = Fixtures::editor($db, ['status' => 'maintainer']);
        $crId = Fixtures::changeRequest($db, $maint, [
            'target_type' => $targetType,
            'target_id'   => $targetId,
            'status'      => 'approved',
        ]);
        return [$crId, $maint];
    }

    /** @return list<array<string, mixed>> */
    private function bridgeRows(int $crId): array
    {
        $st = $this->pdo()->prepare(
            'SELECT * FROM change_request_bridge WHERE change_request_id = ?'
        );
        $st->execute([$crId]);
        /** @var list<array<string, mixed>> $rows */
        $rows = $st->fetchAll();
        return $rows;
    }

    // -----------------------------------------------------------------------
    // bridge_is_bridgeable
    // -----------------------------------------------------------------------

    public function testBridgeableGate(): void
    {
        // reach: text/coord diff, no reach_class -> bridgeable
        $this->assertTrue(bridge_is_bridgeable('reach', ['reach' => ['description' => 'x']]));
        // reach with ANY reach_class key -> NOT bridgeable (adapter rejects it)
        $this->assertFalse(bridge_is_bridgeable(
            'reach',
            ['reach' => ['description' => 'x'], 'reach_class' => ['names' => ['III']]]
        ));
        // even a null reach_class key blocks it (matches the adapter's `in` check)
        $this->assertFalse(bridge_is_bridgeable('reach', ['reach' => ['description' => 'x'], 'reach_class' => null]));
        // empty / missing reach diff -> not bridgeable
        $this->assertFalse(bridge_is_bridgeable('reach', ['reach' => []]));
        $this->assertFalse(bridge_is_bridgeable('reach', []));
        // gauge: non-empty diff -> bridgeable; empty -> not
        $this->assertTrue(bridge_is_bridgeable('gauge', ['gauge' => ['location' => 'x']]));
        $this->assertFalse(bridge_is_bridgeable('gauge', ['gauge' => []]));
        // site / source / unknown -> never bridgeable
        $this->assertFalse(bridge_is_bridgeable('site', ['body' => 'hi']));
        $this->assertFalse(bridge_is_bridgeable('source', ['source' => ['name' => 'x']]));
    }

    // -----------------------------------------------------------------------
    // bridge_enqueue — reach / gauge happy paths
    // -----------------------------------------------------------------------

    public function testEnqueueReachQueuesRowAndCapturesBase(): void
    {
        $db = $this->pdo();
        $reach = Fixtures::reach($db, ['description' => 'old desc', 'river' => 'R']);
        // 'features' is left NULL by the fixture -> base must capture null.
        [$crId, $maint] = $this->seedCr('reach', $reach);

        $applied = ['reach' => ['description' => 'new desc', 'features' => 'a rapid']];
        $json = json_encode($applied, JSON_UNESCAPED_SLASHES);

        $inserted = bridge_enqueue($db, $crId, 'reach', $reach, $applied, (string)$json, $maint);
        $this->assertTrue($inserted);

        $rows = $this->bridgeRows($crId);
        $this->assertCount(1, $rows);
        $row = $rows[0];
        $this->assertSame('queued', $row['state']);
        $this->assertSame(1, (int)$row['attempt']);
        $this->assertSame($maint, (int)$row['queued_by']);
        $this->assertNull($row['base_dataset_sha'], 'PHP leaves the dataset SHA for the worker');
        $this->assertSame(hash('sha256', (string)$json), $row['applied_json_sha256']);

        $base = json_decode((string)$row['reviewed_base_json'], true);
        // base mirrors applied_json's table key, with CURRENT values (the worker's
        // drift base); the untouched-in-DB 'features' is null.
        $this->assertSame('old desc', $base['reach']['description']);
        $this->assertArrayHasKey('features', $base['reach']);
        $this->assertNull($base['reach']['features']);
    }

    public function testEnqueuePreservesWholeNumberFloatInBase(): void
    {
        $db = $this->pdo();
        // optimal_flow is a numeric reach column; PDO returns 800.0 as a PHP float.
        // The base must keep the ".0" so the worker's str(float) drift check matches
        // the dataset CSV's "800.0" rather than false-conflicting on "800".
        $reach = Fixtures::reach($db, ['optimal_flow' => 800.0, 'river' => 'R']);
        [$crId, $maint] = $this->seedCr('reach', $reach);
        $applied = ['reach' => ['optimal_flow' => 900.0]];

        $this->assertTrue(
            bridge_enqueue($db, $crId, 'reach', $reach, $applied, (string)json_encode($applied), $maint)
        );
        $rawBase = (string)$this->bridgeRows($crId)[0]['reviewed_base_json'];
        $this->assertStringContainsString(
            '"optimal_flow":800.0',
            $rawBase,
            'whole-number float base keeps its .0 (JSON_PRESERVE_ZERO_FRACTION)',
        );
    }

    public function testEnqueueGaugeCapturesBase(): void
    {
        $db = $this->pdo();
        $gauge = Fixtures::gauge($db, ['location' => 'Old Spot']);
        [$crId, $maint] = $this->seedCr('gauge', $gauge);

        $applied = ['gauge' => ['location' => 'New Spot']];
        $json = (string)json_encode($applied, JSON_UNESCAPED_SLASHES);

        $this->assertTrue(bridge_enqueue($db, $crId, 'gauge', $gauge, $applied, $json, $maint));
        $rows = $this->bridgeRows($crId);
        $this->assertCount(1, $rows);
        $base = json_decode((string)$rows[0]['reviewed_base_json'], true);
        $this->assertSame('Old Spot', $base['gauge']['location']);
    }

    // -----------------------------------------------------------------------
    // bridge_enqueue — no-op paths (return false, no row)
    // -----------------------------------------------------------------------

    public function testEnqueueSkipsReachClassPayload(): void
    {
        $db = $this->pdo();
        $reach = Fixtures::reach($db, ['river' => 'R']);
        [$crId, $maint] = $this->seedCr('reach', $reach);
        $applied = ['reach' => ['description' => 'x'], 'reach_class' => ['names' => ['IV']]];

        $this->assertFalse(
            bridge_enqueue($db, $crId, 'reach', $reach, $applied, (string)json_encode($applied), $maint)
        );
        $this->assertCount(0, $this->bridgeRows($crId), 'reach_class payload stays manual');
    }

    public function testEnqueueSkipsSiteTarget(): void
    {
        $db = $this->pdo();
        [$crId, $maint] = $this->seedCr('site', null);
        $applied = ['body' => 'a comment'];
        $this->assertFalse(bridge_enqueue($db, $crId, 'site', null, $applied, (string)json_encode($applied), $maint));
        $this->assertCount(0, $this->bridgeRows($crId));
    }

    public function testEnqueueSkipsNullTargetId(): void
    {
        $db = $this->pdo();
        [$crId, $maint] = $this->seedCr('reach', null);
        $applied = ['reach' => ['description' => 'x']];
        $this->assertFalse(bridge_enqueue($db, $crId, 'reach', null, $applied, (string)json_encode($applied), $maint));
        $this->assertCount(0, $this->bridgeRows($crId));
    }

    public function testEnqueueSkipsMissingTargetRow(): void
    {
        $db = $this->pdo();
        [$crId, $maint] = $this->seedCr('reach', 987654);
        $applied = ['reach' => ['description' => 'x']];
        // target reach row doesn't exist -> base capture returns null -> no queue.
        $this->assertFalse(bridge_enqueue($db, $crId, 'reach', 987654, $applied, (string)json_encode($applied), $maint));
        $this->assertCount(0, $this->bridgeRows($crId));
    }

    public function testEnqueueIsIdempotent(): void
    {
        $db = $this->pdo();
        $reach = Fixtures::reach($db, ['description' => 'd', 'river' => 'R']);
        [$crId, $maint] = $this->seedCr('reach', $reach);
        $applied = ['reach' => ['description' => 'new']];
        $json = (string)json_encode($applied, JSON_UNESCAPED_SLASHES);

        $this->assertTrue(bridge_enqueue($db, $crId, 'reach', $reach, $applied, $json, $maint));
        // Second call (re-endorse / retry) is a no-op via ON CONFLICT DO NOTHING.
        $this->assertFalse(bridge_enqueue($db, $crId, 'reach', $reach, $applied, $json, $maint));
        $this->assertCount(1, $this->bridgeRows($crId), 'exactly one bridge row');
    }

    // -----------------------------------------------------------------------
    // bridge_capture_base — defensive guard
    // -----------------------------------------------------------------------

    public function testCaptureBaseRejectsNonBridgeableType(): void
    {
        $this->expectException(InvalidArgumentException::class);
        bridge_capture_base($this->pdo(), 'source', 1, ['source' => ['name' => 'x']]);
    }
}
