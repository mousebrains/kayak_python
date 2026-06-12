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
            "SELECT status, editor_id, reviewed_by, subject, payload_json, applied_json
             FROM change_request
             WHERE target_type = 'reach' AND target_id = " . self::REACH_ID
        )->fetch();
        $this->assertNotFalse($cr, 'direct edit must create a change_request');
        $this->assertSame('approved', $cr['status']);
        $this->assertSame((int)$cr['editor_id'], (int)$cr['reviewed_by'], 'self-endorsed');
        $this->assertStringContainsString('Direct edit:', (string)$cr['subject']);
        $frozen = json_decode((string)$cr['applied_json'], true);
        $this->assertSame('Directly edited description.', $frozen['reach']['description'] ?? null);

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
}
