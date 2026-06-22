<?php

declare(strict_types=1);

require_once __DIR__ . '/IntegrationTestCase.php';

/**
 * End-to-end coverage for the SA-lite direct-edit path (dataset-separation
 * D1): a maintainer POST to /edit.php must freeze the changed fields as a
 * self-endorsed change_request and must NOT write reach/gauge/edit_history —
 * the dataset repo is the only metadata authority, and a direct DB write
 * would be silently reverted by the next deploy's sync-metadata.
 *
 * (#188 review noted edit.php had no direct controller coverage; the auth
 * primitives are pinned separately in EditAuthTest.)
 */
final class EditDirectIntegrationTest extends IntegrationTestCase
{
    private const REACH_ID = 9101;

    protected static function seedDatabase(PDO $db): void
    {
        $db->prepare(
            'INSERT INTO reach
                (id, name, display_name, river, description, sort_name, no_show)
             VALUES (?, ?, ?, ?, ?, ?, ?)'
        )->execute([
            self::REACH_ID,
            'Direct Edit Reach',
            'Direct Edit Reach',
            'Edit River',
            'Original direct description.',
            'direct edit reach',
            0,
        ]);
    }

    public function testDirectEditFreezesSelfEndorsedChangeRequest(): void
    {
        $maint = self::seedEditorSession('direct-edit-maint@example.com', 'maintainer');
        $cookies = [
            'ed_sess' => $maint['session_token'],
            'ed_csrf' => $maint['csrf_token'],
        ];
        $post = [
            'csrf_token' => $maint['csrf_token'],
            'target_type' => 'reach',
            'reach_id' => (string)self::REACH_ID,
            'description' => 'Directly edited description.',
            // The form carries the load-time value as base_<field> (TOCTOU guard);
            // it matches the seeded current, so the edit is accepted.
            'base_description' => 'Original direct description.',
        ];

        $resp = $this->request('/edit.php', [], $cookies, 'POST', $post);

        $this->assertSame(200, $resp['status']);
        $this->assertStringContainsString('Changes Frozen for Data Review', $resp['body']);
        $this->assertStringContainsString('kayak_data', $resp['body']);
        $this->assertStringContainsString('/review.php?id=', $resp['body']);

        $db = self::testDb();

        // The reach row must NOT change (criterion 6).
        $reach = $db->query('SELECT description FROM reach WHERE id = ' . self::REACH_ID)->fetch();
        $this->assertSame('Original direct description.', $reach['description']);

        // A self-endorsed change_request froze the diff.
        $cr = $db->query(
            "SELECT id, status, editor_id, reviewed_by, subject, payload_json, applied_json
             FROM change_request
             WHERE target_type = 'reach' AND target_id = " . self::REACH_ID
        )->fetch();
        $this->assertNotFalse($cr, 'direct edit must create a change_request');
        $this->assertSame('approved', $cr['status']);
        $this->assertSame((int)$cr['editor_id'], (int)$cr['reviewed_by'], 'self-endorsed');
        $this->assertStringContainsString('Direct edit:', (string)$cr['subject']);
        $frozen = json_decode((string)$cr['applied_json'], true);
        $this->assertSame('Directly edited description.', $frozen['reach']['description'] ?? null);

        // Tier 2: the same transaction queued a bridge row for the worker, with
        // the pre-edit value captured as the drift base and the frozen-diff hash.
        $bridge = $db->query(
            'SELECT state, queued_by, base_dataset_sha, reviewed_base_json, applied_json_sha256
             FROM change_request_bridge WHERE change_request_id = ' . (int)$cr['id']
        )->fetch();
        $this->assertNotFalse($bridge, 'direct edit must queue a bridge row');
        $this->assertSame('queued', $bridge['state']);
        $this->assertSame((int)$cr['reviewed_by'], (int)$bridge['queued_by']);
        $this->assertNull($bridge['base_dataset_sha'], 'PHP leaves the dataset SHA to the worker');
        $this->assertSame(
            hash('sha256', (string)$cr['applied_json']),
            $bridge['applied_json_sha256'],
            'bridge pins the frozen-diff hash',
        );
        $base = json_decode((string)$bridge['reviewed_base_json'], true);
        $this->assertSame('Original direct description.', $base['reach']['description'] ?? null);

        // No audit rows: nothing was applied.
        $hist = (int)$db->query(
            "SELECT COUNT(*) FROM edit_history WHERE target_id = " . self::REACH_ID
        )->fetchColumn();
        $this->assertSame(0, $hist);
    }

    public function testDirectEditNoChangesCreatesNothing(): void
    {
        $maint = self::seedEditorSession('direct-noop-maint@example.com', 'maintainer');
        $cookies = [
            'ed_sess' => $maint['session_token'],
            'ed_csrf' => $maint['csrf_token'],
        ];
        $post = [
            'csrf_token' => $maint['csrf_token'],
            'target_type' => 'reach',
            'reach_id' => (string)self::REACH_ID,
            // Same value as seeded — the diff loop must find no changes.
            'description' => 'Original direct description.',
        ];

        $resp = $this->request('/edit.php', [], $cookies, 'POST', $post);

        $this->assertSame(200, $resp['status']);
        $this->assertStringContainsString('No changes to save', $resp['body']);

        $db = self::testDb();
        $count = (int)$db->query(
            "SELECT COUNT(*) FROM change_request WHERE subject LIKE 'Direct edit:%'
             AND editor_id = (SELECT id FROM editor WHERE email = 'direct-noop-maint@example.com')"
        )->fetchColumn();
        $this->assertSame(0, $count, 'a no-op save must not create a change_request');
    }

    public function testDirectEditRejectsStaleBase(): void
    {
        // TOCTOU guard: the field changed since the form loaded (base_description
        // != current) → 409, no change_request frozen.
        $maint = self::seedEditorSession('stale-edit-maint@example.com', 'maintainer');
        $cookies = [
            'ed_sess' => $maint['session_token'],
            'ed_csrf' => $maint['csrf_token'],
        ];
        $post = [
            'csrf_token' => $maint['csrf_token'],
            'target_type' => 'reach',
            'reach_id' => (string)self::REACH_ID,
            'description' => 'My edit',
            'base_description' => 'what I saw earlier',  // != the seeded current
        ];

        $resp = $this->request('/edit.php', [], $cookies, 'POST', $post);

        $this->assertSame(409, $resp['status']);
        $this->assertStringContainsString('changed since you opened', $resp['body']);
        $db = self::testDb();
        $n = (int)$db->query(
            "SELECT COUNT(*) FROM change_request WHERE target_type = 'reach'
             AND target_id = " . self::REACH_ID . " AND subject LIKE 'Direct edit:%'
             AND editor_id = (SELECT id FROM editor WHERE email = 'stale-edit-maint@example.com')"
        )->fetchColumn();
        $this->assertSame(0, $n, 'a stale edit must freeze nothing');
    }

    public function testEditFormRendersTocTouBase(): void
    {
        // Regression: the GET form must carry each field's load-time value as a
        // hidden base_<field>, or the POST drift guard (which compares base_<field>
        // to the current row) finds no base and fail-closes every save. The POST
        // tests above inject base_* directly, so this pins the render side.
        $maint = self::seedEditorSession('render-edit-maint@example.com', 'maintainer');
        $cookies = [
            'ed_sess' => $maint['session_token'],
            'ed_csrf' => $maint['csrf_token'],
        ];

        $resp = $this->request('/edit.php', ['id' => (string)self::REACH_ID], $cookies, 'GET');

        $this->assertSame(200, $resp['status']);
        $this->assertStringContainsString(
            '<input type="hidden" name="base_description" value="Original direct description.">',
            $resp['body'],
            'GET form must carry each field value as a TOCTOU drift base',
        );
    }
}
