<?php

declare(strict_types=1);

require_once __DIR__ . '/IntegrationTestCase.php';

/**
 * /_internal/ — maintainer-only operator dashboard (Phase 2.4).
 *
 * Three auth cases: anonymous → 302 to /login.php?next=..., signed-in
 * non-maintainer → 403 page, maintainer → 200 with dashboard content.
 * The dashboard fans out into 5 sections (build/data freshness,
 * aggregate counts, per-source freshness, CSP violations, quick
 * links); we sample section headings rather than golden-test full
 * markup so the test isn't fragile to copy changes.
 */
class InternalDashboardTest extends IntegrationTestCase
{
    protected static function seedDatabase(PDO $db): void
    {
        // The dashboard renders even with zero rows; no extra seed.
    }

    public function testAnonymousRedirectsToLoginWithNextParam(): void
    {
        $resp = $this->request('/_internal/');

        $this->assertSame(302, $resp['status']);
        $location = $resp['headers']['location'] ?? '';
        $this->assertStringContainsString('/login.php?next=', $location);
        $this->assertStringContainsString('%2F_internal%2F', $location);
    }

    public function testSignedInNonMaintainerGets403(): void
    {
        $sess = self::seedEditorSession('non-maintainer@example.com', 'full');

        $resp = $this->request('/_internal/', cookies: [
            'ed_sess' => $sess['session_token'],
        ]);

        $this->assertSame(403, $resp['status']);
        $this->assertStringContainsString(
            'only available to the site maintainer',
            $resp['body'],
        );
    }

    public function testMaintainerSees200WithDashboardContent(): void
    {
        $sess = self::seedEditorSession('maintainer@example.com', 'maintainer');

        $resp = $this->request('/_internal/', cookies: [
            'ed_sess' => $sess['session_token'],
        ]);

        $this->assertSame(200, $resp['status']);
        $this->assertResponseContains(
            $resp['body'],
            'Internal dashboard',
            'Build + data freshness',
            'Per-source freshness',
            'Aggregate counts',
        );
        // CSP-safe: dashboard uses inline <style> (style-src allows it)
        // but no inline <script> — verify the script-src guard still holds.
        $this->assertNoBareInlineScript($resp['body']);
        // X-Robots-Tag: PHP sets it via header(); nginx also sets it in
        // production. Either source counts for this test.
        $this->assertStringContainsString(
            'noindex',
            $resp['headers']['x-robots-tag'] ?? '',
        );
    }
}
