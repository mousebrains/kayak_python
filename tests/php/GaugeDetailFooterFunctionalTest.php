<?php

declare(strict_types=1);

require_once __DIR__ . '/FunctionalTestCase.php';
require_once __DIR__ . '/Fixtures.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/db.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/header.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/footer.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/validate.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/gauge_detail.php';

use PHPUnit\Framework\Attributes\PreserveGlobalState;
use PHPUnit\Framework\Attributes\RunInSeparateProcess;

/**
 * Footer editor-button coverage for gauge_detail.php's
 * _render_gauge_footer().
 *
 * The footer's "Edit" button hinges on editor_feature_enabled() +
 * current_editor() + is_maintainer(). current_editor() caches its
 * result in a process-static, so a single process can observe only one
 * editor state. Each test here runs in its own process
 * (RunInSeparateProcess + PreserveGlobalState(false)) so the static
 * cache starts cold per branch — same pattern as
 * DescriptionFooterFunctionalTest.
 *
 * gauge_detail's footer has only the maintainer branch (gauge proposals
 * aren't supported by propose.php yet, so non-maintainer editors see no
 * extra button). The two tests pin the maintainer-Edit-shown branch and
 * the non-maintainer-no-Edit branch (the is_maintainer() false path).
 * The editor-feature-disabled path is covered by the in-process
 * GaugeDetailFunctionalTest.
 */
final class GaugeDetailFooterFunctionalTest extends FunctionalTestCase
{
    private static int $gaugeId = 0;

    protected static function seedDatabase(PDO $db): void
    {
        // A bare gauge is enough — only the footer branch matters here.
        self::$gaugeId = Fixtures::gauge($db, [
            'name' => 'FOOTER_GAUGE',
            'display_name' => 'Footer Gauge',
        ]);
    }

    /** Set a valid session cookie for an editor of the given status. */
    private function loginAs(string $status): void
    {
        $editorId = Fixtures::editor($this->pdo(), [
            'email' => $status . '@example.com',
            'status' => $status,
        ]);
        $tok = str_repeat('c', 64);                          // 64 hex chars
        $this->pdo()->prepare(
            "INSERT INTO editor_session (editor_id, token_hash, expires_at, revoked_at)
             VALUES (?, ?, datetime('now', '+7 days'), NULL)"
        )->execute([$editorId, hash('sha256', $tok)]);
        $_COOKIE[EDITOR_SESSION_COOKIE] = $tok;
    }

    #[RunInSeparateProcess]
    #[PreserveGlobalState(false)]
    public function testFooterMaintainerEditButton(): void
    {
        Config::install_for_tests(['editor_feature' => true]);
        $this->loginAs('maintainer');
        $html = $this->capture(
            fn() => handle_gauge_detail($this->pdo(), self::$gaugeId, null, null)
        );
        // Maintainer → the gauge-typed Edit anchor renders.
        $this->assertStringContainsString('/edit.php?h=' . pubhash_encode(self::$gaugeId) . '&amp;type=gauge', $html);
    }

    #[RunInSeparateProcess]
    #[PreserveGlobalState(false)]
    public function testFooterNonMaintainerNoEditButton(): void
    {
        Config::install_for_tests(['editor_feature' => true]);
        $this->loginAs('full');                              // non-maintainer editor
        $html = $this->capture(
            fn() => handle_gauge_detail($this->pdo(), self::$gaugeId, null, null)
        );
        // editor_feature on + signed in but not a maintainer → is_maintainer()
        // false → no Edit button (gauges have no "Suggest" path).
        $this->assertStringNotContainsString('/edit.php?', $html);
        // The standard footer links still render.
        $this->assertStringContainsString('All gauges', $html);
    }
}
