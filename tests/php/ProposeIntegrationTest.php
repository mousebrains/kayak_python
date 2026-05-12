<?php

declare(strict_types=1);

require_once __DIR__ . '/IntegrationTestCase.php';

/**
 * Baseline integration tests for propose.php (Phase 5.P.1 of
 * php_layer_split). Editor-gated entry point — first test class to
 * exercise the new `seedEditorSession` helper in IntegrationTestCase.
 *
 * Covers:
 *  - Anonymous → 302 to /login.php?next=...
 *  - Maintainer → 302 to /edit.php?id=N (maintainers skip the queue)
 *  - Editor GET → 200 with form (csrf field present, reach name in
 *    title)
 *  - 400 on missing id
 *  - 400 on type other than 'reach' (Phase 2 only supports reach)
 *  - POST with invalid CSRF → 403
 *  - POST with honeypot filled → 200 "saved" but NO change_request row
 *  - POST with valid CSRF + reach edit → 200 "saved" + change_request
 *    row with the proposed payload
 *
 * Each editor case uses a fresh seedEditorSession() — gives each test
 * its own editor row + session token, so we can assert on per-editor
 * change_request counts without test-order coupling.
 */
final class ProposeIntegrationTest extends IntegrationTestCase
{
    private const REACH_ID = 5001;
    private const REACH_NAME = 'Propose Test Reach';

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
            'Propose River',
            'Original description.',
            'propose test reach',
            0,
        ]);
    }

    public function testAnonymousRedirectsToLogin(): void
    {
        $resp = $this->request('/propose.php', ['type' => 'reach', 'id' => self::REACH_ID]);

        $this->assertSame(302, $resp['status']);
        $this->assertStringStartsWith(
            '/login.php',
            $resp['headers']['location'] ?? '',
        );
    }

    public function testMaintainerRedirectsToEdit(): void
    {
        $auth = self::seedEditorSession('maintainer@example.com', 'maintainer');
        $cookies = ['ed_sess' => $auth['session_token']];

        $resp = $this->request('/propose.php', ['type' => 'reach', 'id' => self::REACH_ID], $cookies);

        $this->assertSame(302, $resp['status']);
        $this->assertSame(
            '/edit.php?id=' . self::REACH_ID,
            $resp['headers']['location'] ?? '',
        );
    }

    public function testEditorGetRendersForm(): void
    {
        $auth = self::seedEditorSession('editor-get@example.com', 'full');
        $cookies = ['ed_sess' => $auth['session_token']];

        $resp = $this->request('/propose.php', ['type' => 'reach', 'id' => self::REACH_ID], $cookies);

        $this->assertSame(200, $resp['status']);
        $this->assertResponseContains(
            $resp['body'],
            'Suggest an edit',
            self::REACH_NAME,
            'csrf_token',
            '<form',
            // Exact tag wrap — bare 'full' could collide with words like
            // 'useful' or 'successful' in surrounding copy.
            '<strong>full</strong>',
        );
        $this->assertNoBareInlineScript($resp['body']);
    }

    public function testMissingIdReturns400(): void
    {
        $auth = self::seedEditorSession('editor-noid@example.com', 'full');
        $cookies = ['ed_sess' => $auth['session_token']];

        $resp = $this->request('/propose.php', ['type' => 'reach'], $cookies);

        $this->assertSame(400, $resp['status']);
        $this->assertStringContainsString('Missing id parameter', $resp['body']);
    }

    public function testInvalidTypeReturns400(): void
    {
        $auth = self::seedEditorSession('editor-type@example.com', 'full');
        $cookies = ['ed_sess' => $auth['session_token']];

        $resp = $this->request('/propose.php', ['type' => 'gauge', 'id' => self::REACH_ID], $cookies);

        $this->assertSame(400, $resp['status']);
        $this->assertStringContainsString('Only reach proposals supported', $resp['body']);
    }

    public function testPostInvalidCsrfReturns403(): void
    {
        $auth = self::seedEditorSession('editor-csrf@example.com', 'full');
        $cookies = ['ed_sess' => $auth['session_token'], 'ed_csrf' => $auth['csrf_token']];
        $post = [
            'csrf_token' => 'a-completely-different-64-char-hex-token-not-the-real-one-12345678',
            'target_type' => 'reach',
            'target_id' => (string)self::REACH_ID,
            'description' => 'attempted change',
        ];

        $resp = $this->request('/propose.php', [], $cookies, 'POST', $post);

        $this->assertSame(403, $resp['status']);
        $this->assertStringContainsString('Invalid CSRF token', $resp['body']);
        // No change_request row created on CSRF failure.
        $db = self::testDb();
        $count = (int)$db->query(
            'SELECT COUNT(*) FROM change_request WHERE editor_id = ' . (int)$auth['editor_id']
        )->fetchColumn();
        $this->assertSame(0, $count);
    }

    public function testPostHoneypotSilentlyAccepts(): void
    {
        // Bot fills the hidden `website` field — propose.php pretends it
        // saved (returns the "saved" success page) but writes nothing.
        $auth = self::seedEditorSession('editor-honey@example.com', 'full');
        $cookies = ['ed_sess' => $auth['session_token'], 'ed_csrf' => $auth['csrf_token']];
        $post = [
            'csrf_token' => $auth['csrf_token'],
            'target_type' => 'reach',
            'target_id' => (string)self::REACH_ID,
            'website' => 'http://spam.example.com',  // honeypot trip
            'description' => 'spam content here',
        ];

        $resp = $this->request('/propose.php', [], $cookies, 'POST', $post);

        $this->assertSame(200, $resp['status']);
        $this->assertStringContainsString('your proposal was recorded', $resp['body']);
        // The "save" was a lie — no change_request row exists.
        $db = self::testDb();
        $count = (int)$db->query(
            'SELECT COUNT(*) FROM change_request WHERE editor_id = ' . (int)$auth['editor_id']
        )->fetchColumn();
        $this->assertSame(0, $count);
    }

    public function testPostValidCreatesChangeRequest(): void
    {
        $auth = self::seedEditorSession('editor-save@example.com', 'full');
        $cookies = ['ed_sess' => $auth['session_token'], 'ed_csrf' => $auth['csrf_token']];
        $post = [
            'csrf_token' => $auth['csrf_token'],
            'target_type' => 'reach',
            'target_id' => (string)self::REACH_ID,
            'description' => 'A new description proposed via integration test.',
        ];

        $resp = $this->request('/propose.php', [], $cookies, 'POST', $post);

        $this->assertSame(200, $resp['status']);
        $this->assertStringContainsString('your proposal was recorded', $resp['body']);
        $this->assertNoBareInlineScript($resp['body']);

        $db = self::testDb();
        $cr = $db->query(
            'SELECT * FROM change_request WHERE editor_id = ' . (int)$auth['editor_id'] . ' ORDER BY id DESC LIMIT 1'
        )->fetch();
        $this->assertIsArray($cr);
        $this->assertSame('pending', $cr['status']);
        $this->assertSame('reach', $cr['target_type']);
        $this->assertSame(self::REACH_ID, (int)$cr['target_id']);
        $payload = json_decode((string)$cr['payload_json'], true);
        $this->assertIsArray($payload);
        $this->assertSame(
            'A new description proposed via integration test.',
            $payload['reach']['description'] ?? null,
        );
    }
}
