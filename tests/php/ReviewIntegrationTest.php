<?php

declare(strict_types=1);

require_once __DIR__ . '/IntegrationTestCase.php';

/**
 * Baseline integration tests for review.php (Phase 5.R.1 of
 * php_layer_split). Maintainer-only moderation queue.
 *
 * Reuses the seedEditorSession('…','maintainer') helper added in
 * 5.P.1 so per-test maintainer + per-CR scenarios stay isolated.
 *
 * Covers:
 *  - Anonymous → 302 to /login.php?next=…
 *  - Editor (full, non-maintainer) → 403 not-allowed page
 *  - Maintainer GET / (no id) → list view with status filter row
 *  - Maintainer GET ?id=N → detail view with editable form (csrf + reach fields)
 *  - Maintainer GET ?id=99999 → 404
 *  - Maintainer POST approve with valid CSRF → 200 + applies change + flash
 *  - Maintainer POST approve with invalid CSRF → 403 (CSRF double-submit fails)
 *  - Maintainer POST reject → 200 + CR moves to 'rejected'
 *  - Maintainer POST approve already-rejected → 200 + flash error, no apply
 *
 * Seed: 1 reach + per-test change_request rows seeded inside each
 * test (so the test that mutates state doesn't interfere with the
 * test that checks the rejection flash, etc.).
 */
final class ReviewIntegrationTest extends IntegrationTestCase
{
    private const REACH_ID = 9001;
    private const REACH_NAME = 'Review Test Reach';

    protected static function seedDatabase(PDO $db): void
    {
        $db->prepare(
            'INSERT INTO reach
                (id, name, display_name, river, description, sort_name, no_show)
             VALUES (?, ?, ?, ?, ?, ?, ?)'
        )->execute([
            self::REACH_ID,
            self::REACH_NAME,
            self::REACH_NAME,
            'Review River',
            'Original description.',
            'review test reach',
            0,
        ]);
    }

    /** Seed a pending change_request proposing a description tweak. */
    private static function seedPendingCR(
        int $editor_id,
        string $proposed_description = 'A reviewer-proposed description.',
        string $status = 'pending',
    ): int {
        $db = self::testDb();
        $payload = json_encode([
            'reach' => ['description' => $proposed_description],
        ]);
        $db->prepare(
            "INSERT INTO change_request
                (target_type, target_id, editor_id, subject, payload_json,
                 status, submitted_at)
             VALUES (?, ?, ?, ?, ?, ?, datetime('now'))"
        )->execute([
            'reach',
            self::REACH_ID,
            $editor_id,
            'Description tweak',
            $payload,
            $status,
        ]);
        return (int)$db->lastInsertId();
    }

    public function testAnonymousRedirectsToLogin(): void
    {
        $resp = $this->request('/review.php');

        $this->assertSame(302, $resp['status']);
        $this->assertStringStartsWith('/login.php', $resp['headers']['location'] ?? '');
    }

    public function testNonMaintainerEditorGets403(): void
    {
        $auth = self::seedEditorSession('reviewer-editor@example.com', 'full');
        $cookies = ['ed_sess' => $auth['session_token']];

        $resp = $this->request('/review.php', [], $cookies);

        $this->assertSame(403, $resp['status']);
        $this->assertStringContainsString('Not allowed', $resp['body']);
    }

    public function testMaintainerGetListRenders(): void
    {
        $maint = self::seedEditorSession('list-maint@example.com', 'maintainer');
        $editor = self::seedEditorSession('list-editor@example.com', 'full');
        $cr_id = self::seedPendingCR($editor['editor_id']);
        $cookies = ['ed_sess' => $maint['session_token']];

        $resp = $this->request('/review.php', [], $cookies);

        $this->assertSame(200, $resp['status']);
        $this->assertResponseContains(
            $resp['body'],
            'Review queue',
            'list-editor@example.com',
            'Description tweak',
            // Status filter strip — all five options rendered as links.
            '/review.php?status=pending',
            '/review.php?status=resolved',
            // CR row links to the detail view.
            '/review.php?id=' . $cr_id,
        );
        $this->assertNoBareInlineScript($resp['body']);
    }

    public function testMaintainerGetDetailRendersForm(): void
    {
        // Order-independence: testPostApproveAppliesChange mutates the shared
        // reach's description, and executionOrder="defects" (result cache) can
        // run it first — pin the precondition this test asserts on rather than
        // depending on declaration order (latent flake caught 2026-06-12).
        self::testDb()->prepare('UPDATE reach SET description = ? WHERE id = ?')
            ->execute(['Original description.', self::REACH_ID]);
        $maint = self::seedEditorSession('detail-maint@example.com', 'maintainer');
        $editor = self::seedEditorSession('detail-editor@example.com', 'full');
        $cr_id = self::seedPendingCR($editor['editor_id'], 'NEW DETAIL TEXT');
        $cookies = ['ed_sess' => $maint['session_token']];

        $resp = $this->request('/review.php', ['id' => $cr_id], $cookies);

        $this->assertSame(200, $resp['status']);
        $this->assertResponseContains(
            $resp['body'],
            'Review: Description tweak',
            'detail-editor@example.com',
            'csrf_token',
            // The current vs proposed table renders both values.
            'Original description.',
            'NEW DETAIL TEXT',
            // Approve button visible on pending reach proposals.
            'value="approve"',
            'value="reject"',
        );
        $this->assertNoBareInlineScript($resp['body']);
    }

    public function testMaintainerGet404OnMissingCR(): void
    {
        $maint = self::seedEditorSession('404-maint@example.com', 'maintainer');
        $cookies = ['ed_sess' => $maint['session_token']];

        $resp = $this->request('/review.php', ['id' => 9999999], $cookies);

        $this->assertSame(404, $resp['status']);
        $this->assertStringContainsString('No change request with id 9999999', $resp['body']);
    }

    public function testPostInvalidCsrfReturns403(): void
    {
        $maint = self::seedEditorSession('csrf-maint@example.com', 'maintainer');
        $editor = self::seedEditorSession('csrf-editor@example.com', 'full');
        $cr_id = self::seedPendingCR($editor['editor_id']);
        $cookies = [
            'ed_sess' => $maint['session_token'],
            'ed_csrf' => $maint['csrf_token'],
        ];
        $post = [
            'csrf_token' => 'a-different-token-that-does-not-match-cookie-0123456789abcdef',
            'id' => (string)$cr_id,
            'action' => 'approve',
        ];

        $resp = $this->request('/review.php', [], $cookies, 'POST', $post);

        $this->assertSame(403, $resp['status']);
        $this->assertStringContainsString('Invalid CSRF token', $resp['body']);

        $db = self::testDb();
        $row = $db->query("SELECT status FROM change_request WHERE id = $cr_id")->fetch();
        $this->assertSame('pending', $row['status']);
    }

    public function testPostApproveEndorsesWithoutWriting(): void
    {
        // SA-lite (dataset-separation D1): approve = endorse for data review.
        // The diff freezes in applied_json; the reach row must NOT change —
        // the dataset repo is the only metadata authority (criterion 6).
        $maint = self::seedEditorSession('approve-maint@example.com', 'maintainer');
        $editor = self::seedEditorSession('approve-editor@example.com', 'full');
        $cr_id = self::seedPendingCR($editor['editor_id'], 'APPROVED DESCRIPTION VALUE');
        // Pin the current description so the TOCTOU base check is deterministic.
        self::testDb()->prepare('UPDATE reach SET description = ? WHERE id = ?')
            ->execute(['Original description.', self::REACH_ID]);
        $cookies = [
            'ed_sess' => $maint['session_token'],
            'ed_csrf' => $maint['csrf_token'],
        ];
        $post = [
            'csrf_token' => $maint['csrf_token'],
            'id' => (string)$cr_id,
            'action' => 'approve',
            // Approve form mirrors payload fields back as reach_<field>, and carries
            // the render-time "Current" value as base_reach_<field> (TOCTOU guard).
            'reach_description' => 'APPROVED DESCRIPTION VALUE',
            'base_reach_description' => 'Original description.',
            'reviewer_note' => 'Looks good — endorsing.',
        ];

        $resp = $this->request('/review.php', [], $cookies, 'POST', $post);

        $this->assertSame(200, $resp['status']);
        $this->assertStringContainsString('Endorsed', $resp['body']);
        $this->assertStringContainsString('kayak_data', $resp['body']);

        // CR transitioned and applied_json froze the endorsed payload.
        $db = self::testDb();
        $cr = $db->query("SELECT status, applied_json FROM change_request WHERE id = $cr_id")->fetch();
        $this->assertSame('approved', $cr['status']);
        $applied = json_decode((string)$cr['applied_json'], true);
        $this->assertSame('APPROVED DESCRIPTION VALUE', $applied['reach']['description'] ?? null);

        // The reach row did NOT pick up the description; no audit rows either.
        $reach = $db->query('SELECT description FROM reach WHERE id = ' . self::REACH_ID)->fetch();
        $this->assertNotSame('APPROVED DESCRIPTION VALUE', $reach['description']);

        // Tier 2: endorsing this bridgeable reach diff queued a bridge row in the
        // same transaction, capturing the unchanged reach value as the drift base.
        $bridge = $db->query(
            "SELECT state, queued_by, reviewed_base_json, applied_json_sha256
             FROM change_request_bridge WHERE change_request_id = $cr_id"
        )->fetch();
        $this->assertNotFalse($bridge, 'endorsing a reach diff must queue a bridge row');
        $this->assertSame('queued', $bridge['state']);
        $this->assertSame($maint['editor_id'], (int)$bridge['queued_by']);
        $this->assertSame(hash('sha256', (string)$cr['applied_json']), $bridge['applied_json_sha256']);
        $base = json_decode((string)$bridge['reviewed_base_json'], true);
        $this->assertSame($reach['description'], $base['reach']['description'] ?? '__missing__');
        $hist = (int)$db->query(
            "SELECT COUNT(*) FROM edit_history WHERE change_request_id = $cr_id"
        )->fetchColumn();
        $this->assertSame(0, $hist);

        // Close the loop: Mark resolved (deployed) from the endorsed view.
        $resolve = [
            'csrf_token' => $maint['csrf_token'],
            'id' => (string)$cr_id,
            'action' => 'resolve',
        ];
        $resp2 = $this->request('/review.php', [], $cookies, 'POST', $resolve);
        $this->assertSame(200, $resp2['status']);
        $cr2 = $db->query("SELECT status FROM change_request WHERE id = $cr_id")->fetch();
        $this->assertSame('resolved', $cr2['status']);
    }

    public function testPostApproveRejectsStaleBase(): void
    {
        // TOCTOU guard: the reach changed since the maintainer opened the page
        // (base_reach_description != current) → approve must be refused, the CR
        // left pending, and no bridge row queued.
        $maint = self::seedEditorSession('stale-maint@example.com', 'maintainer');
        $editor = self::seedEditorSession('stale-editor@example.com', 'full');
        $cr_id = self::seedPendingCR($editor['editor_id'], 'NEW VALUE');
        self::testDb()->prepare('UPDATE reach SET description = ? WHERE id = ?')
            ->execute(['the value NOW', self::REACH_ID]);
        $cookies = ['ed_sess' => $maint['session_token'], 'ed_csrf' => $maint['csrf_token']];
        $post = [
            'csrf_token' => $maint['csrf_token'],
            'id' => (string)$cr_id,
            'action' => 'approve',
            'reach_description' => 'NEW VALUE',
            'base_reach_description' => 'what I saw earlier',  // != 'the value NOW'
        ];

        $resp = $this->request('/review.php', [], $cookies, 'POST', $post);

        $this->assertSame(200, $resp['status']);
        $this->assertStringContainsString('changed since you opened', $resp['body']);
        $db = self::testDb();
        $cr = $db->query("SELECT status FROM change_request WHERE id = $cr_id")->fetch();
        $this->assertSame('pending', $cr['status'], 'a stale approve must not endorse');
        $n = (int)$db->query(
            "SELECT COUNT(*) FROM change_request_bridge WHERE change_request_id = $cr_id"
        )->fetchColumn();
        $this->assertSame(0, $n, 'no bridge row for a refused approve');
    }

    public function testPostRejectMarksRejected(): void
    {
        $maint = self::seedEditorSession('reject-maint@example.com', 'maintainer');
        $editor = self::seedEditorSession('reject-editor@example.com', 'full');
        $cr_id = self::seedPendingCR($editor['editor_id']);
        $cookies = [
            'ed_sess' => $maint['session_token'],
            'ed_csrf' => $maint['csrf_token'],
        ];
        $post = [
            'csrf_token' => $maint['csrf_token'],
            'id' => (string)$cr_id,
            'action' => 'reject',
            'reviewer_note' => 'Out of scope for now.',
        ];

        $resp = $this->request('/review.php', [], $cookies, 'POST', $post);

        $this->assertSame(200, $resp['status']);
        $this->assertStringContainsString('Rejected', $resp['body']);

        $db = self::testDb();
        $cr = $db->query("SELECT status, reviewer_note FROM change_request WHERE id = $cr_id")->fetch();
        $this->assertSame('rejected', $cr['status']);
        $this->assertStringContainsString('Out of scope for now', (string)$cr['reviewer_note']);
    }

    public function testPostReplyKeepsPending(): void
    {
        $maint = self::seedEditorSession('reply-maint@example.com', 'maintainer');
        $editor = self::seedEditorSession('reply-editor@example.com', 'full');
        $cr_id = self::seedPendingCR($editor['editor_id']);
        $cookies = [
            'ed_sess' => $maint['session_token'],
            'ed_csrf' => $maint['csrf_token'],
        ];
        $post = [
            'csrf_token' => $maint['csrf_token'],
            'id' => (string)$cr_id,
            'action' => 'reply',
            'reviewer_note' => 'Need more detail on the rapid description.',
        ];

        $resp = $this->request('/review.php', [], $cookies, 'POST', $post);

        $this->assertSame(200, $resp['status']);
        $this->assertStringContainsString('Reply sent', $resp['body']);

        // Reply does not transition the CR — it stays 'pending'.
        $db = self::testDb();
        $cr = $db->query("SELECT status, reviewer_note FROM change_request WHERE id = $cr_id")->fetch();
        $this->assertSame('pending', $cr['status']);
        $this->assertStringContainsString('Need more detail', (string)$cr['reviewer_note']);
    }

    public function testPostReplyEmptyNoteShowsError(): void
    {
        // Empty-note short-circuit before review_send_reply runs — applies
        // to both `reply` and `reply_and_close`.
        $maint = self::seedEditorSession('empty-maint@example.com', 'maintainer');
        $editor = self::seedEditorSession('empty-editor@example.com', 'full');
        $cr_id = self::seedPendingCR($editor['editor_id']);
        $cookies = [
            'ed_sess' => $maint['session_token'],
            'ed_csrf' => $maint['csrf_token'],
        ];
        $post = [
            'csrf_token' => $maint['csrf_token'],
            'id' => (string)$cr_id,
            'action' => 'reply',
            'reviewer_note' => '   ',  // whitespace only — trim() makes it empty
        ];

        $resp = $this->request('/review.php', [], $cookies, 'POST', $post);

        $this->assertSame(200, $resp['status']);
        $this->assertStringContainsString('Reply cannot be empty', $resp['body']);

        $db = self::testDb();
        $cr = $db->query("SELECT status, reviewer_note FROM change_request WHERE id = $cr_id")->fetch();
        $this->assertSame('pending', $cr['status']);
        $this->assertNull($cr['reviewer_note']);
    }

    public function testPostReplyAndCloseMarksResolved(): void
    {
        $maint = self::seedEditorSession('rac-maint@example.com', 'maintainer');
        $editor = self::seedEditorSession('rac-editor@example.com', 'full');
        $cr_id = self::seedPendingCR($editor['editor_id']);
        $cookies = [
            'ed_sess' => $maint['session_token'],
            'ed_csrf' => $maint['csrf_token'],
        ];
        $post = [
            'csrf_token' => $maint['csrf_token'],
            'id' => (string)$cr_id,
            'action' => 'reply_and_close',
            'reviewer_note' => 'Tracked elsewhere — closing.',
        ];

        $resp = $this->request('/review.php', [], $cookies, 'POST', $post);

        $this->assertSame(200, $resp['status']);
        $this->assertStringContainsString('proposal marked resolved', $resp['body']);

        $db = self::testDb();
        $cr = $db->query("SELECT status, reviewer_note FROM change_request WHERE id = $cr_id")->fetch();
        $this->assertSame('resolved', $cr['status']);
        $this->assertStringContainsString('Tracked elsewhere', (string)$cr['reviewer_note']);
    }

    public function testPostResolveMarksResolved(): void
    {
        $maint = self::seedEditorSession('resolve-maint@example.com', 'maintainer');
        $editor = self::seedEditorSession('resolve-editor@example.com', 'full');
        $cr_id = self::seedPendingCR($editor['editor_id']);
        $cookies = [
            'ed_sess' => $maint['session_token'],
            'ed_csrf' => $maint['csrf_token'],
        ];
        $post = [
            'csrf_token' => $maint['csrf_token'],
            'id' => (string)$cr_id,
            'action' => 'resolve',
            'reviewer_note' => 'Site comment — no action needed.',
        ];

        $resp = $this->request('/review.php', [], $cookies, 'POST', $post);

        $this->assertSame(200, $resp['status']);
        $this->assertStringContainsString('Marked resolved', $resp['body']);

        $db = self::testDb();
        $cr = $db->query("SELECT status, reviewer_note FROM change_request WHERE id = $cr_id")->fetch();
        $this->assertSame('resolved', $cr['status']);
        $this->assertStringContainsString('no action needed', (string)$cr['reviewer_note']);
    }

    public function testPostOnAlreadyReviewedShowsFlashError(): void
    {
        // Seed a CR that's already 'rejected' — the controller should
        // short-circuit with a flash error before the action dispatch.
        $maint = self::seedEditorSession('already-maint@example.com', 'maintainer');
        $editor = self::seedEditorSession('already-editor@example.com', 'full');
        $cr_id = self::seedPendingCR($editor['editor_id'], 'irrelevant', 'rejected');
        $cookies = [
            'ed_sess' => $maint['session_token'],
            'ed_csrf' => $maint['csrf_token'],
        ];
        $post = [
            'csrf_token' => $maint['csrf_token'],
            'id' => (string)$cr_id,
            'action' => 'approve',
            'reach_description' => 'attempted late approval',
        ];

        $resp = $this->request('/review.php', [], $cookies, 'POST', $post);

        $this->assertSame(200, $resp['status']);
        $this->assertStringContainsString(
            'This request has already been rejected',
            $resp['body'],
        );

        // The CR is still 'rejected' (no transition) and applied_json
        // is still null (no apply happened). Assert on CR state, not
        // the reach row — earlier-running tests in this class may have
        // mutated the shared seed reach.
        $db = self::testDb();
        $cr = $db->query("SELECT status, applied_json FROM change_request WHERE id = $cr_id")->fetch();
        $this->assertSame('rejected', $cr['status']);
        $this->assertNull($cr['applied_json']);
    }
}
