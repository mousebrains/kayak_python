<?php

declare(strict_types=1);

require_once __DIR__ . '/IntegrationTestCase.php';

/**
 * Drill test for the integration scaffold (Phase 1.4 of php_layer_split).
 *
 * Exercises reach.php through the test server end-to-end:
 *  - HTTP 200 on a valid state-filter URL.
 *  - Body includes substrings only the response template emits.
 *  - No CSP header set PHP-side (production CSP comes from nginx; PHP must
 *    not set its own header() that would shadow it in prod).
 *  - No inline <script> tag (a regression here would clash with the
 *    nginx-side strict CSP).
 *
 * This is intentionally a *narrow* test — Tier 2's baseline tests (Phase
 * 2.1) will expand coverage to all three reach.php modes (search / list /
 * detail) before the split work begins.
 */
final class ReachIntegrationTest extends IntegrationTestCase
{
    public function testStateFilterListRenders(): void
    {
        // Oregon is seeded by `levels init-db` (_seed_states in init_db.py).
        // The DB has no reach rows post-init, so this exercises the empty
        // "0 reaches matching OR" path through the list-mode branch.
        $resp = $this->request('/reach.php', ['st' => 'OR']);

        $this->assertSame(200, $resp['status'], 'reach.php?st=OR should return 200');
        // Templates emit the search-result count line either as "0 reaches"
        // or "reaches matching" — assert both fragments to pin the renderer.
        $this->assertResponseContains(
            $resp['body'],
            'reaches matching',  // count + "reaches matching" substring
            '</html>',           // page actually finished rendering
        );
        // PHP must not emit its own CSP header; nginx owns that policy.
        $this->assertArrayNotHasKey('content-security-policy', $resp['headers']);
        // CSP-safety regression guard — no inline script tags.
        $this->assertStringNotContainsString(
            '<script>',
            $resp['body'],
            'inline <script> would clash with prod CSP',
        );
    }
}
