<?php

declare(strict_types=1);

require_once __DIR__ . '/FunctionalTestCase.php';
require_once __DIR__ . '/Fixtures.php';
require_once __DIR__ . '/../../php/includes/db.php';
require_once __DIR__ . '/../../php/includes/header.php';
require_once __DIR__ . '/../../php/includes/footer.php';
require_once __DIR__ . '/../../php/includes/validate.php';
require_once __DIR__ . '/../../php/includes/description_detail.php';

use PHPUnit\Framework\Attributes\PreserveGlobalState;
use PHPUnit\Framework\Attributes\RunInSeparateProcess;

/**
 * Footer editor-button coverage for description_detail.php's
 * _render_description_footer().
 *
 * The footer's three editor branches (maintainer "Edit" / non-maintainer
 * "Suggest an edit" / anonymous "Sign in") all hinge on current_editor(),
 * which caches its result in a static per process. A single process can
 * therefore only observe ONE of those states. Each test here runs in its
 * own process (RunInSeparateProcess + PreserveGlobalState(false)) so the
 * static cache starts cold and each branch is reachable. The bootstrap +
 * a fresh seeded DB are re-created per isolated test by the base class's
 * setUpBeforeClass.
 *
 * Kept in a dedicated class so the bulk DescriptionDetailFunctionalTest
 * stays in-process (fast, no isolation overhead). The reach + gauge here
 * are deliberately minimal — only the footer branch matters.
 */
final class DescriptionFooterFunctionalTest extends FunctionalTestCase
{
    private static int $reachId = 0;

    protected static function seedDatabase(PDO $db): void
    {
        // A no-gauge reach is enough — the footer renders regardless of the
        // page body, and skipping the gauge keeps the render cheap.
        self::$reachId = Fixtures::reach($db, [
            'name' => 'Footer Reach',
            'display_name' => 'Footer Reach',
            'sort_name' => 'footer reach',
            'description' => 'Footer branch fixture.',
        ]);
    }

    /** Set a valid session cookie for an editor of the given status. */
    private function loginAs(string $status): void
    {
        $editorId = Fixtures::editor($this->pdo(), [
            'email' => $status . '@example.com',
            'status' => $status,
        ]);
        $tok = str_repeat('b', 64);                         // 64 hex chars
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
            fn() => handle_description_detail($this->pdo(), self::$reachId, null, null, 0)
        );
        $this->assertStringContainsString('/edit.php?h=' . pubhash_encode(self::$reachId), $html);
        $this->assertStringNotContainsString('Suggest an edit', $html);
    }

    #[RunInSeparateProcess]
    #[PreserveGlobalState(false)]
    public function testFooterEditorSuggestButton(): void
    {
        Config::install_for_tests(['editor_feature' => true]);
        $this->loginAs('full');                              // non-maintainer editor
        $html = $this->capture(
            fn() => handle_description_detail($this->pdo(), self::$reachId, null, null, 0)
        );
        $this->assertStringContainsString('Suggest an edit', $html);
        $this->assertStringContainsString('/propose.php?type=reach&amp;h=' . pubhash_encode(self::$reachId), $html);
        $this->assertStringNotContainsString('/edit.php?', $html);
    }

    #[RunInSeparateProcess]
    #[PreserveGlobalState(false)]
    public function testFooterAnonymousSignInButton(): void
    {
        Config::install_for_tests(['editor_feature' => true]);
        // No session cookie → current_editor() returns null → anonymous branch.
        $html = $this->capture(
            fn() => handle_description_detail($this->pdo(), self::$reachId, null, null, 0)
        );
        $this->assertStringContainsString('Sign in to suggest an edit', $html);
        $this->assertStringContainsString('/login.php?next=', $html);
    }
}
