<?php

declare(strict_types=1);

require_once __DIR__ . '/FunctionalTestCase.php';
require_once __DIR__ . '/Fixtures.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/db.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/auth.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/header.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/footer.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/review_handler.php';

/**
 * In-process functional coverage for review_handler.php — the render +
 * dispatch layer for the maintainer review queue.
 *
 * Coverage seam note: handle_review_request() resolves ?id= via
 * filter_input(INPUT_GET/INPUT_POST, …), which the CLI SAPI never populates
 * from a test-set $_GET/$_POST — so the in-process dispatcher only ever takes
 * the list-view branch. The id-routed detail view + the POST action routing
 * (where id is read via filter_input) are covered over real HTTP in
 * ReviewIntegrationTest. Here we:
 *   - drive handle_review_request() for the list view (status filter + rows),
 *   - call the POST-dispatch helper _review_handle_post() directly (explicit
 *     $cr_id/$action args, bypassing filter_input) for every action branch,
 *   - call _render_review_detail() directly for the pending-form, terminal,
 *     and 404 branches,
 *   - call _review_build_approve_payload() for both the with/without
 *     classes_present shapes.
 *
 * require_csrf() (auth.php) reads $_POST['csrf_token'] + the ed_csrf cookie
 * directly (not via filter_input), so a matching double-submit pair drives it
 * fine in-process. Email is routed to MAIL_DUMP_DIR.
 *
 * Tier-1 / mutating: action tests seed their own editor + reach + CR inside
 * the method (shared per-class DB) and assert the transition landed.
 */
final class ReviewHandlerFunctionalTest extends FunctionalTestCase
{
    private const CSRF = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';

    private static string $mailDir = '';

    public static function setUpBeforeClass(): void
    {
        self::$mailDir = sys_get_temp_dir() . '/kayak-revhandler-mail-' . getmypid();
        if (!is_dir(self::$mailDir)) {
            mkdir(self::$mailDir, 0700, true);
        }
        putenv('MAIL_DUMP_DIR=' . self::$mailDir);
        Config::install_for_tests(['mail_dump_dir' => self::$mailDir]);
        parent::setUpBeforeClass();
    }

    public static function tearDownAfterClass(): void
    {
        parent::tearDownAfterClass();
        Config::install_for_tests([]);
        putenv('MAIL_DUMP_DIR');
        if (self::$mailDir !== '' && is_dir(self::$mailDir)) {
            array_map('unlink', glob(self::$mailDir . '/*') ?: []);
            @rmdir(self::$mailDir);
        }
        self::$mailDir = '';
    }

    /** The maintainer acting on the queue. */
    private function maint(): array
    {
        $id = Fixtures::editor($this->pdo(), ['status' => 'maintainer']);
        return ['id' => $id, 'status' => 'maintainer', 'email' => "maint$id@example.com"];
    }

    /** Seed a pending reach-edit CR and return [crId, reachId, editorId]. */
    private function seedScenario(array $payload, array $crOverrides = []): array
    {
        $db = $this->pdo();
        $editor = Fixtures::editor($db); // fixture auto-uniquifies the email
        $reach = Fixtures::reach($db, ['river' => 'Handler River', 'description' => 'old']);
        $crId = Fixtures::changeRequest($db, $editor, $crOverrides + [
            'target_id' => $reach,
            'payload_json' => json_encode($payload, JSON_UNESCAPED_SLASHES),
            'subject' => 'Proposed edit: Handler Reach',
        ]);
        return [$crId, $reach, $editor];
    }

    private function fetchCr(int $crId): array
    {
        $st = $this->pdo()->prepare('SELECT * FROM change_request WHERE id = ?');
        $st->execute([$crId]);
        /** @var array<string, mixed> $row */
        $row = $st->fetch();
        return $row;
    }

    /** Set up a valid double-submit-CSRF POST environment. */
    private function withCsrfPost(array $fields): void
    {
        $_SERVER['REQUEST_METHOD'] = 'POST';
        $_COOKIE[EDITOR_CSRF_COOKIE] = self::CSRF;
        $_POST = $fields + ['csrf_token' => self::CSRF];
    }

    // -----------------------------------------------------------------------
    // handle_review_request — list view (the in-process-reachable dispatch)
    // -----------------------------------------------------------------------

    public function testListViewRendersDefaultPendingFilter(): void
    {
        $this->seedScenario(['reach' => ['description' => 'pending one']]);
        $html = $this->capture(fn() => handle_review_request($this->pdo(), $this->maint()));
        $this->assertStringContainsString('Review queue', $html);
        // Status filter row present, with pending bolded as the default.
        $this->assertStringContainsString('/review.php?status=pending', $html);
        $this->assertStringContainsString('/review.php?status=all', $html);
        $this->assertStringContainsString('Proposed edit: Handler Reach', $html);
    }

    public function testListViewInvalidStatusFallsBackToPending(): void
    {
        $_GET['status'] = 'bogus-filter';
        $html = $this->capture(fn() => handle_review_request($this->pdo(), $this->maint()));
        // Bad filter coerced to 'pending' (bolded), still a valid page.
        $this->assertStringContainsString('Review queue', $html);
        $this->assertStringContainsString('style="font-weight:700"', $html);
    }

    public function testListViewAllStatusShowsTerminalRows(): void
    {
        [$crId] = $this->seedScenario(['reach' => ['description' => 'x']], ['status' => 'rejected']);
        $_GET['status'] = 'all';
        $html = $this->capture(fn() => handle_review_request($this->pdo(), $this->maint()));
        $this->assertStringContainsString('Review queue', $html);
        // The 'all' filter drops the WHERE clause so rejected rows show too.
        $this->assertStringContainsString('rejected', $html);
    }

    public function testListViewEmptyResultShowsNoProposals(): void
    {
        // 'approved' filter with no approved rows -> empty-state message.
        $_GET['status'] = 'approved';
        // Guard: only assert the empty state if there really are no approved
        // rows in the shared DB at this point.
        $cnt = (int)$this->pdo()->query("SELECT COUNT(*) FROM change_request WHERE status='approved'")->fetchColumn();
        $html = $this->capture(fn() => handle_review_request($this->pdo(), $this->maint()));
        if ($cnt === 0) {
            $this->assertStringContainsString('No proposals.', $html);
        } else {
            $this->assertStringContainsString('Review queue', $html);
        }
    }

    public function testListViewSkipsPostDispatchOnGet(): void
    {
        // A GET with no id must not run _review_handle_post (no CSRF needed).
        $_SERVER['REQUEST_METHOD'] = 'GET';
        $html = $this->capture(fn() => handle_review_request($this->pdo(), $this->maint()));
        $this->assertStringContainsString('Review queue', $html);
    }

    // -----------------------------------------------------------------------
    // _review_handle_post — hard-failure early-outs (now via http_terminate)
    // -----------------------------------------------------------------------

    public function testPostMissingIdIs400(): void
    {
        $this->withCsrfPost([]);
        $e = $this->captureExit(fn() => _review_handle_post($this->pdo(), null, 'approve', $this->maint()['id']));
        $this->assertSame(400, $e->statusCode);
    }

    public function testPostUnknownCrIs404(): void
    {
        $this->withCsrfPost([]);
        $e = $this->captureExit(fn() => _review_handle_post($this->pdo(), 999999, 'approve', $this->maint()['id']));
        $this->assertSame(404, $e->statusCode);
    }

    public function testPostAlreadyReviewedReturnsFlashError(): void
    {
        [$crId] = $this->seedScenario(['reach' => ['description' => 'x']], ['status' => 'approved']);
        $this->withCsrfPost([]);
        [$flash, $err] = _review_handle_post($this->pdo(), $crId, 'approve', $this->maint()['id']);
        $this->assertNull($flash);
        $this->assertStringContainsString('already been approved', (string)$err);
    }

    // -----------------------------------------------------------------------
    // _review_handle_post — action branches
    // -----------------------------------------------------------------------

    public function testPostApproveAppliesAndFlashes(): void
    {
        [$crId, $reachId] = $this->seedScenario(['reach' => ['description' => 'approved via handler']]);
        $maint = $this->maint();
        // base_reach_* carries the render-time "Current" value (= the seeded 'old')
        // for the TOCTOU guard; it matches, so approve proceeds.
        $this->withCsrfPost(['reviewer_note' => 'ok', 'base_reach_description' => 'old']);

        [$flash, $err] = _review_handle_post($this->pdo(), $crId, 'approve', $maint['id']);
        $this->assertNull($err);
        $this->assertSame('Endorsed — the diff is frozen below. Land it as a kayak_data PR, then Mark resolved once the deploy ships it.', $flash);

        $cr = $this->fetchCr($crId);
        $this->assertSame('approved', $cr['status']);
        // SA-lite: the diff is frozen, the reach is NOT written.
        $this->assertStringContainsString('approved via handler', (string)$cr['applied_json']);
        $st = $this->pdo()->prepare('SELECT description FROM reach WHERE id = ?');
        $st->execute([$reachId]);
        $this->assertNotSame('approved via handler', $st->fetchColumn(), 'endorse must not write');
        // Editor was emailed the decision.
        $this->assertGreaterThan(0, count(glob(self::$mailDir . '/*') ?: []));
    }

    public function testPostApproveWithMaintainerOverlayWins(): void
    {
        [$crId, $reachId] = $this->seedScenario(['reach' => ['description' => 'editor wording']]);
        $maint = $this->maint();
        // Maintainer edits the proposed value via the reach_<field> overlay.
        $this->withCsrfPost([
            'reach_description' => 'maintainer wording',
            'reviewer_note' => '',
            'base_reach_description' => 'old',  // matches the seeded current (TOCTOU guard)
        ]);

        [$flash] = _review_handle_post($this->pdo(), $crId, 'approve', $maint['id']);
        $this->assertSame('Endorsed — the diff is frozen below. Land it as a kayak_data PR, then Mark resolved once the deploy ships it.', $flash);
        // The maintainer overlay wins in the FROZEN diff (nothing is applied).
        $applied = json_decode((string)$this->fetchCr($crId)['applied_json'], true);
        $this->assertSame('maintainer wording', $applied['reach']['description']);
        $st = $this->pdo()->prepare('SELECT description FROM reach WHERE id = ?');
        $st->execute([$reachId]);
        $this->assertNotSame('maintainer wording', $st->fetchColumn(), 'endorse must not write');
    }

    public function testPostApproveRejectsStaleBase(): void
    {
        // TOCTOU guard: the carried base_reach_description ('stale') no longer
        // matches the current reach value ('old'), so approve is refused and the
        // CR stays pending (no endorse against a stale view).
        [$crId] = $this->seedScenario(['reach' => ['description' => 'proposed']]);
        $this->withCsrfPost(['reach_description' => 'proposed', 'base_reach_description' => 'stale']);

        [$flash, $err] = _review_handle_post($this->pdo(), $crId, 'approve', $this->maint()['id']);
        $this->assertNull($flash);
        $this->assertStringContainsString('changed since you opened', (string)$err);
        $this->assertSame('pending', $this->fetchCr($crId)['status']);
    }

    public function testPostApproveWithClassBlockOverlay(): void
    {
        $db = $this->pdo();
        $editor = Fixtures::editor($db);
        $reach = Fixtures::reach($db, ['river' => 'Class Handler River']);
        $payload = ['reach_class' => ['names' => ['III'], 'range' => ['low' => 100, 'high' => 200, 'data_type' => 'flow']]];
        $crId = Fixtures::changeRequest($db, $editor, [
            'target_id' => $reach,
            'payload_json' => json_encode($payload),
            'subject' => 'class change',
        ]);
        $maint = $this->maint();
        // classes_present unlocks the maintainer-editable class block.
        $this->withCsrfPost([
            'classes_present' => '1',
            'classes' => 'IV, V',
            'flow_low' => '700',
            'flow_high' => '1500',
            'flow_data_type' => 'gauge',
        ]);

        [$flash] = _review_handle_post($db, $crId, 'approve', $maint['id']);
        $this->assertSame('Endorsed — the diff is frozen below. Land it as a kayak_data PR, then Mark resolved once the deploy ships it.', $flash);
        // The class overlay lands in the FROZEN diff; reach_class rows untouched.
        $applied = json_decode((string)$this->fetchCr($crId)['applied_json'], true);
        $this->assertSame(['IV', 'V'], $applied['reach_class']['names']);
        $this->assertSame('gauge', $applied['reach_class']['range']['data_type']);
        $st = $db->prepare('SELECT COUNT(*) FROM reach_class WHERE reach_id = ?');
        $st->execute([$reach]);
        $this->assertSame(0, (int)$st->fetchColumn(), 'endorse must not write classes');
    }

    public function testPostApproveTargetMissingReturnsErrFlash(): void
    {
        $db = $this->pdo();
        $editor = Fixtures::editor($db);
        $crId = Fixtures::changeRequest($db, $editor, [
            'target_id' => 654321, // no such reach
            'payload_json' => json_encode(['reach' => ['description' => 'x']]),
        ]);
        $this->withCsrfPost([]);
        [$flash, $err] = _review_handle_post($db, $crId, 'approve', $this->maint()['id']);
        $this->assertNull($flash);
        $this->assertSame('Target missing', $err);
    }

    public function testPostReject(): void
    {
        [$crId] = $this->seedScenario(['reach' => ['description' => 'x']]);
        $this->withCsrfPost(['reviewer_note' => 'no thanks']);
        [$flash, $err] = _review_handle_post($this->pdo(), $crId, 'reject', $this->maint()['id']);
        $this->assertNull($err);
        $this->assertSame('Rejected.', $flash);
        $this->assertSame('rejected', $this->fetchCr($crId)['status']);
    }

    public function testPostResolve(): void
    {
        [$crId] = $this->seedScenario(['reach' => ['description' => 'x']]);
        $this->withCsrfPost(['reviewer_note' => 'mooted']);
        [$flash, $err] = _review_handle_post($this->pdo(), $crId, 'resolve', $this->maint()['id']);
        $this->assertNull($err);
        $this->assertSame('Marked resolved.', $flash);
        $this->assertSame('resolved', $this->fetchCr($crId)['status']);
    }

    public function testPostReplyKeepsPending(): void
    {
        [$crId] = $this->seedScenario(['reach' => ['description' => 'x']]);
        $this->withCsrfPost(['reviewer_note' => 'a question']);
        [$flash, $err] = _review_handle_post($this->pdo(), $crId, 'reply', $this->maint()['id']);
        $this->assertNull($err);
        $this->assertStringContainsString('kept pending', (string)$flash);
        $this->assertSame('pending', $this->fetchCr($crId)['status']);
    }

    public function testPostReplyEmptyNoteIsError(): void
    {
        [$crId] = $this->seedScenario(['reach' => ['description' => 'x']]);
        $this->withCsrfPost(['reviewer_note' => '   ']);
        [$flash, $err] = _review_handle_post($this->pdo(), $crId, 'reply', $this->maint()['id']);
        $this->assertNull($flash);
        $this->assertStringContainsString('cannot be empty', (string)$err);
        $this->assertSame('pending', $this->fetchCr($crId)['status'], 'no transition on empty reply');
    }

    public function testPostReplyAndClose(): void
    {
        [$crId] = $this->seedScenario(['reach' => ['description' => 'x']]);
        $this->withCsrfPost(['reviewer_note' => 'closing']);
        [$flash, $err] = _review_handle_post($this->pdo(), $crId, 'reply_and_close', $this->maint()['id']);
        $this->assertNull($err);
        $this->assertStringContainsString('marked resolved', (string)$flash);
        $this->assertSame('resolved', $this->fetchCr($crId)['status']);
    }

    public function testPostReplyAndCloseEmptyNoteIsError(): void
    {
        [$crId] = $this->seedScenario(['reach' => ['description' => 'x']]);
        $this->withCsrfPost(['reviewer_note' => '']);
        [$flash, $err] = _review_handle_post($this->pdo(), $crId, 'reply_and_close', $this->maint()['id']);
        $this->assertNull($flash);
        $this->assertStringContainsString('cannot be empty', (string)$err);
    }

    public function testPostUnknownActionRendersNoFlash(): void
    {
        [$crId] = $this->seedScenario(['reach' => ['description' => 'x']]);
        $this->withCsrfPost([]);
        // No matching case in the switch -> [null, null], CR untouched.
        [$flash, $err] = _review_handle_post($this->pdo(), $crId, 'frobnicate', $this->maint()['id']);
        $this->assertNull($flash);
        $this->assertNull($err);
        $this->assertSame('pending', $this->fetchCr($crId)['status']);
    }

    // -----------------------------------------------------------------------
    // _review_build_approve_payload
    // -----------------------------------------------------------------------

    public function testBuildApprovePayloadReachOverlayAndDefault(): void
    {
        $cr = ['payload_json' => json_encode(['reach' => ['description' => 'orig', 'features' => 'orig f']])];
        // One field overridden in POST, the other defaults to the proposal value.
        $_POST = ['reach_description' => 'tweaked'];
        $applied = _review_build_approve_payload($cr);
        $this->assertSame('tweaked', $applied['reach']['description']);
        $this->assertSame('orig f', $applied['reach']['features']);
        // No classes_present -> reach_class key removed entirely.
        $this->assertArrayNotHasKey('reach_class', $applied);
    }

    public function testBuildApprovePayloadClassBlock(): void
    {
        $cr = ['payload_json' => json_encode(['reach_class' => ['names' => ['III'], 'range' => ['low' => 1, 'high' => 2, 'data_type' => 'flow']]])];
        $_POST = [
            'classes_present' => '1',
            'classes' => 'IV, , V', // empty token filtered out
            'flow_low' => '300',
            'flow_high' => '',
            'flow_data_type' => 'gauge',
        ];
        $applied = _review_build_approve_payload($cr);
        $this->assertSame(['IV', 'V'], $applied['reach_class']['names']);
        $this->assertEqualsWithDelta(300.0, $applied['reach_class']['range']['low'], 0.001);
        $this->assertNull($applied['reach_class']['range']['high'], 'blank high -> null');
        $this->assertSame('gauge', $applied['reach_class']['range']['data_type']);
    }

    public function testBuildApprovePayloadMalformedJson(): void
    {
        // Non-array decode -> [] payload -> empty applied.reach, no class block.
        $cr = ['payload_json' => 'not json at all'];
        $_POST = [];
        $applied = _review_build_approve_payload($cr);
        $this->assertSame([], $applied['reach']);
        $this->assertArrayNotHasKey('reach_class', $applied);
    }

    // -----------------------------------------------------------------------
    // _render_review_detail — pending form / terminal / 404
    // -----------------------------------------------------------------------

    public function testRenderDetailFormShowsEarlierNotesAndTextInputField(): void
    {
        [$crId] = $this->seedScenario(
            // display_name is a non-long reach field -> renders as <input type=text>
            // (not a textarea), exercising the else branch of the reach-fields render.
            ['reach' => ['display_name' => 'Proposed Name']],
            ['reviewer_note' => '[2026-01-01 00:00Z maintainer] an earlier note thread'],
        );
        $html = $this->capture(fn() => _render_review_detail($this->pdo(), $crId, null, null, self::CSRF));
        $this->assertStringContainsString('name="reach_display_name"', $html);
        $this->assertStringContainsString('type="text"', $html, 'non-long field uses a text input');
        // Prior reviewer_note thread shown under the decision textarea.
        $this->assertStringContainsString('Earlier notes:', $html);
        $this->assertStringContainsString('an earlier note thread', $html);
    }

    public function testRenderDetailPendingShowsEditableForm(): void
    {
        [$crId] = $this->seedScenario([
            'reach' => ['description' => 'a proposed change'],
            'reach_class' => ['names' => ['IV'], 'range' => ['low' => 500, 'high' => 1000, 'data_type' => 'flow']],
        ]);
        $html = $this->capture(fn() => _render_review_detail($this->pdo(), $crId, 'flashed!', null, self::CSRF));
        $this->assertStringContainsString('Review:', $html);
        $this->assertStringContainsString('flashed!', $html, 'success flash rendered');
        // Editable approve form with reach + class blocks + decision buttons.
        $this->assertStringContainsString('name="reach_description"', $html);
        $this->assertStringContainsString('Classes and flow range (editable)', $html);
        $this->assertStringContainsString('value="approve"', $html);
        $this->assertStringContainsString('value="reject"', $html);
        $this->assertStringContainsString(self::CSRF, $html);
    }

    public function testRenderDetailEmitsTocTouBaseForReachFields(): void
    {
        // Regression: the pending form must emit a hidden base_reach_<field>
        // carrying the rendered "Current" value for every proposed reach field.
        // Without it the POST drift guard (_review_check_base_drift) finds no base
        // and fail-closes every endorse. The earlier POST tests injected
        // base_reach_* directly, so they never exercised the render side — this
        // pins it. seedScenario seeds the reach with description 'old'.
        [$crId] = $this->seedScenario(['reach' => ['description' => 'a proposed change']]);
        $html = $this->capture(fn() => _render_review_detail($this->pdo(), $crId, null, null, self::CSRF));
        $this->assertStringContainsString(
            '<input type="hidden" name="base_reach_description" value="old">',
            $html,
            'each proposed reach field must carry its current value as a TOCTOU drift base',
        );
    }

    public function testRenderDetailTerminalShowsAppliedPayloadNoForm(): void
    {
        [$crId] = $this->seedScenario(
            ['reach' => ['description' => 'x']],
            [
                'status' => 'approved',
                'reviewer_note' => 'applied this',
                'applied_json' => json_encode(['reach' => ['description' => 'final text']]),
            ],
        );
        $html = $this->capture(fn() => _render_review_detail($this->pdo(), $crId, null, 'something went wrong', self::CSRF));
        $this->assertStringContainsString('something went wrong', $html, 'error flash rendered');
        $this->assertStringContainsString('Maintainer notes', $html);
        $this->assertStringContainsString('applied this', $html);
        $this->assertStringContainsString('Endorsed changes (frozen for data review)', $html);
        $this->assertStringContainsString('final text', $html);
        // No approve button — but an endorsed request offers the SA-lite
        // close-the-loop action with the land-it instructions.
        $this->assertStringNotContainsString('value="approve"', $html);
        $this->assertStringContainsString('kayak_data', $html);
        $this->assertStringContainsString('value="resolve"', $html);
        $this->assertStringContainsString('Mark resolved (deployed)', $html);
    }

    public function testRenderDetailResolvedShowsNoFormAtAll(): void
    {
        [$crId] = $this->seedScenario(
            ['reach' => ['description' => 'x']],
            ['status' => 'resolved', 'reviewer_note' => 'done'],
        );
        $html = $this->capture(fn() => _render_review_detail($this->pdo(), $crId, null, null, self::CSRF));
        $this->assertStringNotContainsString('value="approve"', $html);
        $this->assertStringNotContainsString('value="resolve"', $html);
    }

    public function testRenderDetailUnknownIdRenders404(): void
    {
        $e = $this->captureExit(fn() => _render_review_detail($this->pdo(), 888888, null, null, self::CSRF));
        $this->assertSame(404, $e->statusCode);
    }

    public function testRenderDetailClassBlockToleratesMalformedShape(): void
    {
        // payload.reach_class is an array (so the block renders) but its
        // names/range sub-keys are NOT arrays — exercises the defensive
        // `is_array(...) ? ... : default` false-branches in
        // _render_review_class_block without throwing.
        [$crId] = $this->seedScenario(['reach_class' => ['names' => 'not-a-list', 'range' => 'not-a-map']]);
        $html = $this->capture(fn() => _render_review_detail($this->pdo(), $crId, null, null, self::CSRF));
        $this->assertStringContainsString('Classes and flow range (editable)', $html);
        // Falls back to an empty classes input + default flow range row.
        $this->assertStringContainsString('name="classes"', $html);
        $this->assertStringContainsString('name="flow_data_type"', $html);
    }

    public function testRenderDetailNotesAndSourceUrlRows(): void
    {
        [$crId] = $this->seedScenario(
            ['reach' => ['description' => 'x'], 'body' => 'a free-form message body'],
            ['notes_to_maint' => 'a note for the maintainer', 'source_url' => '/description.php?id=1'],
        );
        $html = $this->capture(fn() => _render_review_detail($this->pdo(), $crId, null, null, self::CSRF));
        // Meta table optional rows: Page (source_url), Message (payload.body), Notes.
        $this->assertStringContainsString('a note for the maintainer', $html);
        $this->assertStringContainsString('/description.php?id=1', $html);
        $this->assertStringContainsString('a free-form message body', $html);
    }
}
