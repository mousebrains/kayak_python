<?php

declare(strict_types=1);

require_once __DIR__ . '/FunctionalTestCase.php';
require_once __DIR__ . '/Fixtures.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/review_logic.php';

/**
 * In-process functional coverage for review_logic.php — the apply/notify
 * helpers behind the maintainer review flow.
 *
 * ReviewApproveRaceTest already covers the two-maintainer race path
 * (review_approve's conditional-UPDATE claim) + the reach-column apply +
 * edit_history happy path against a hand-rolled minimal schema. This class
 * runs against the *real* init-db schema and fills the gaps pcov shows:
 *   - review_approve's reach_class replace-set branch + edit_history row
 *   - the "target missing" and "apply failed" (caught-throwable) bail-outs
 *   - review_reject / review_resolve / review_reply_and_close transitions
 *     (and their already-reviewed no-op returns)
 *   - review_send_reply (status-preserving) + review_notify_editor with and
 *     without a deliverable editor email
 *   - review_load_target_state (non-reach type, missing reach, range pick)
 *   - merge_reviewer_note threading
 *
 * Tier-1 / mutating: every test seeds its own editor + reach + CR inside the
 * method (the class shares one DB), then asserts the DB landed.
 *
 * send_email() is neutralized to MAIL_DUMP_DIR (a tmp dir) so the notify
 * paths run end-to-end without invoking the real mail() transport.
 */
final class ReviewLogicFunctionalTest extends FunctionalTestCase
{
    private static string $mailDir = '';

    public static function setUpBeforeClass(): void
    {
        self::$mailDir = sys_get_temp_dir() . '/kayak-revlogic-mail-' . getmypid();
        if (!is_dir(self::$mailDir)) {
            mkdir(self::$mailDir, 0700, true);
        }
        putenv('MAIL_DUMP_DIR=' . self::$mailDir);
        // Config reads mail_dump_dir from env via Config::str's env fallback?
        // No — Config has no env fallback. mail_dump_dir() reads Config::str,
        // which returns '' here, so re-seed the singleton with the dump dir so
        // send_email writes to a file instead of calling mail().
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

    /** Insert a pending CR and return its full row in the documented $cr shape. */
    private function seedCr(int $editorId, int $reachId, array $payload, array $overrides = []): array
    {
        $crId = Fixtures::changeRequest($this->pdo(), $editorId, $overrides + [
            'target_id'    => $reachId,
            'payload_json' => json_encode($payload, JSON_UNESCAPED_SLASHES),
            'subject'      => 'Proposed edit: Test',
        ]);
        return $this->fetchCr($crId);
    }

    private function fetchCr(int $crId): array
    {
        $st = $this->pdo()->prepare('SELECT * FROM change_request WHERE id = ?');
        $st->execute([$crId]);
        /** @var array<string, mixed> $row */
        $row = $st->fetch();
        return $row;
    }

    private function historyCount(int $crId): int
    {
        $st = $this->pdo()->prepare('SELECT COUNT(*) FROM edit_history WHERE change_request_id = ?');
        $st->execute([$crId]);
        return (int)$st->fetchColumn();
    }

    // -----------------------------------------------------------------------
    // review_approve — SA-lite (D1): endorse + freeze, never mutate metadata
    // -----------------------------------------------------------------------

    public function testApproveFreezesDiffWithoutWritingMetadata(): void
    {
        $db = $this->pdo();
        $maint = Fixtures::editor($db, ['status' => 'maintainer']);
        $editor = Fixtures::editor($db);
        $reach = Fixtures::reach($db, [
            'name' => 'Approve Reach',
            'description' => 'old desc',
            'river' => 'Test River',
        ]);
        $payload = ['reach' => ['description' => 'brand new desc', 'features' => 'a rapid']];
        $cr = $this->seedCr($editor, $reach, $payload);

        $applied = ['reach' => ['description' => 'brand new desc', 'features' => 'a rapid']];
        $res = review_approve($db, $cr, $applied, $maint, 'looks good');

        $this->assertTrue($res['ok'], 'endorse must succeed');

        // SA-lite: the reach row is NOT touched — the dataset repo is the only
        // metadata authority (a direct write would be reverted by the next
        // deploy's sync-metadata). This assertion is the criterion-6 guard.
        $st = $db->prepare('SELECT description, features, updated_at FROM reach WHERE id = ?');
        $st->execute([$reach]);
        $row = $st->fetch();
        $this->assertSame('old desc', $row['description'], 'reach column must NOT change');
        $this->assertNull($row['features'], 'reach column must NOT change');
        $this->assertNull($row['updated_at'], 'updated_at must NOT be stamped');

        // change_request claimed: status -> approved (endorsed), note merged,
        // applied_json carries the frozen maintainer-edited diff.
        $crAfter = $this->fetchCr($cr['id']);
        $this->assertSame('approved', $crAfter['status']);
        $this->assertSame($maint, (int)$crAfter['reviewed_by']);
        $this->assertNotNull($crAfter['reviewed_at']);
        $this->assertStringContainsString('looks good', (string)$crAfter['reviewer_note']);
        $this->assertStringContainsString('maintainer]', (string)$crAfter['reviewer_note']);
        $applied_json = json_decode((string)$crAfter['applied_json'], true);
        $this->assertSame('brand new desc', $applied_json['reach']['description']);
        $this->assertSame('a rapid', $applied_json['reach']['features']);

        // No audit rows: nothing was applied.
        $this->assertSame(0, $this->historyCount($cr['id']));
    }

    public function testApproveFreezesBlankOverlayAndLeavesColumn(): void
    {
        $db = $this->pdo();
        $maint = Fixtures::editor($db, ['status' => 'maintainer']);
        $editor = Fixtures::editor($db);
        $reach = Fixtures::reach($db, ['description' => 'something', 'river' => 'R']);
        $cr = $this->seedCr($editor, $reach, ['reach' => ['description' => '']]);

        // The blank overlay freezes verbatim (the empty-string -> NULL collapse
        // is now the dataset editor's call when landing the CSV change).
        $res = review_approve($db, $cr, ['reach' => ['description' => '']], $maint, '');
        $this->assertTrue($res['ok']);

        $st = $db->prepare('SELECT description FROM reach WHERE id = ?');
        $st->execute([$reach]);
        $this->assertSame('something', $st->fetchColumn(), 'reach untouched');
        $applied_json = json_decode((string)$this->fetchCr($cr['id'])['applied_json'], true);
        $this->assertSame('', $applied_json['reach']['description']);
    }

    public function testApproveDropsNonAllowlistedReachField(): void
    {
        $db = $this->pdo();
        $maint = Fixtures::editor($db, ['status' => 'maintainer']);
        $editor = Fixtures::editor($db);
        $reach = Fixtures::reach($db, ['description' => 'old desc', 'river' => 'R']);

        // Tampered payload: a legit field plus two non-proposable reach columns.
        // R1.4 carried into SA-lite: forged keys must not survive into the
        // FROZEN diff either — an operator copies applied_json into the
        // dataset, so a forged key would otherwise launder itself into a
        // reviewed CSV edit.
        $applied = ['reach' => [
            'description' => 'new desc',  // allowlisted -> frozen
            'no_show'     => 1,           // real column, not proposable -> dropped
            'id'          => 999999,      // dropped
        ]];
        $cr = $this->seedCr($editor, $reach, $applied);

        $res = review_approve($db, $cr, $applied, $maint, '');
        $this->assertTrue($res['ok'], 'forged keys are dropped, not fatal');

        $applied_json = json_decode((string)$this->fetchCr($cr['id'])['applied_json'], true);
        $this->assertSame(['description' => 'new desc'], $applied_json['reach']);

        // Nothing applied anywhere.
        $st = $db->prepare('SELECT description, no_show FROM reach WHERE id = ?');
        $st->execute([$reach]);
        $row = $st->fetch();
        $this->assertSame('old desc', $row['description']);
        $this->assertSame(0, $this->historyCount($cr['id']));
    }

    public function testApproveFreezesReachClassWithoutReplacingRows(): void
    {
        $db = $this->pdo();
        $maint = Fixtures::editor($db, ['status' => 'maintainer']);
        $editor = Fixtures::editor($db);
        $reach = Fixtures::reach($db, ['river' => 'Class River']);
        // Pre-existing classes must SURVIVE — pre-SA-lite approve replaced them.
        Fixtures::reachClass($db, $reach, ['name' => 'II', 'low' => 100.0, 'low_data_type' => 'flow', 'high' => 200.0, 'high_data_type' => 'flow']);
        Fixtures::reachClass($db, $reach, ['name' => 'II+']);

        $applied = [
            'reach' => [],
            'reach_class' => [
                'names' => ['III', 'III+', 'IV'],
                'range' => ['low' => 300.0, 'high' => 800.0, 'data_type' => 'flow'],
            ],
        ];
        $cr = $this->seedCr($editor, $reach, $applied);

        $res = review_approve($db, $cr, $applied, $maint, '');
        $this->assertTrue($res['ok']);

        $st = $db->prepare('SELECT name FROM reach_class WHERE reach_id = ? ORDER BY id');
        $st->execute([$reach]);
        $this->assertSame(['II', 'II+'], array_column($st->fetchAll(), 'name'), 'classes untouched');

        $applied_json = json_decode((string)$this->fetchCr($cr['id'])['applied_json'], true);
        $this->assertSame(['III', 'III+', 'IV'], $applied_json['reach_class']['names']);
        $this->assertSame(0, $this->historyCount($cr['id']));
    }

    public function testResolveClosesEndorsedRequest(): void
    {
        // SA-lite loop closure: after the dataset PR deploys, the maintainer
        // marks the endorsed (approved) request resolved.
        $db = $this->pdo();
        $maint = Fixtures::editor($db, ['status' => 'maintainer']);
        $editor = Fixtures::editor($db);
        $reach = Fixtures::reach($db, ['river' => 'R']);
        $cr = $this->seedCr($editor, $reach, ['reach' => ['description' => 'x']]);
        $this->assertTrue(review_approve($db, $cr, ['reach' => ['description' => 'x']], $maint, '')['ok']);

        $this->assertTrue(review_resolve($db, $this->fetchCr($cr['id']), 'deployed', $maint));
        $this->assertSame('resolved', $this->fetchCr($cr['id'])['status']);
    }

    public function testApproveTargetMissingReturnsError(): void
    {
        $db = $this->pdo();
        $maint = Fixtures::editor($db, ['status' => 'maintainer']);
        $editor = Fixtures::editor($db);
        // CR points at a non-existent reach id.
        $cr = $this->seedCr($editor, 987654, ['reach' => ['description' => 'x']]);

        $res = review_approve($db, $cr, ['reach' => ['description' => 'x']], $maint, '');
        $this->assertFalse($res['ok']);
        $this->assertSame('Target missing', $res['err']);
        // CR untouched.
        $this->assertSame('pending', $this->fetchCr($cr['id'])['status']);
    }

    // -----------------------------------------------------------------------
    // review_reject / resolve / reply_and_close transitions + no-op races
    // -----------------------------------------------------------------------

    public function testRejectTransitionsPendingToRejected(): void
    {
        $db = $this->pdo();
        $maint = Fixtures::editor($db, ['status' => 'maintainer']);
        $editor = Fixtures::editor($db);
        $reach = Fixtures::reach($db, ['river' => 'R']);
        $cr = $this->seedCr($editor, $reach, ['reach' => ['description' => 'x']], ['reviewer_note' => 'prior note']);

        $ok = review_reject($db, $cr, 'not a real change', $maint);
        $this->assertTrue($ok);

        $after = $this->fetchCr($cr['id']);
        $this->assertSame('rejected', $after['status']);
        $this->assertSame($maint, (int)$after['reviewed_by']);
        // merge_reviewer_note appended onto the prior thread.
        $this->assertStringContainsString('prior note', (string)$after['reviewer_note']);
        $this->assertStringContainsString('not a real change', (string)$after['reviewer_note']);
    }

    public function testRejectOnAlreadyReviewedReturnsFalse(): void
    {
        $db = $this->pdo();
        $maint = Fixtures::editor($db, ['status' => 'maintainer']);
        $editor = Fixtures::editor($db);
        $reach = Fixtures::reach($db, ['river' => 'R']);
        $cr = $this->seedCr($editor, $reach, ['reach' => ['description' => 'x']], ['status' => 'approved']);

        $this->assertFalse(review_reject($db, $cr, 'late', $maint));
        $this->assertSame('approved', $this->fetchCr($cr['id'])['status']);
    }

    public function testResolveTransitionsAndRaceReturnsFalse(): void
    {
        $db = $this->pdo();
        $maint = Fixtures::editor($db, ['status' => 'maintainer']);
        $editor = Fixtures::editor($db);
        $reach = Fixtures::reach($db, ['river' => 'R']);
        $cr = $this->seedCr($editor, $reach, ['reach' => ['description' => 'x']]);

        $this->assertTrue(review_resolve($db, $cr, 'mooted', $maint));
        $this->assertSame('resolved', $this->fetchCr($cr['id'])['status']);

        // Second call on the same (now-stale-pending) $cr loses.
        $this->assertFalse(review_resolve($db, $cr, 'again', $maint));
    }

    public function testReplyAndCloseResolvesAndEmailsEditor(): void
    {
        $db = $this->pdo();
        $maint = Fixtures::editor($db, ['status' => 'maintainer']);
        $editor = Fixtures::editor($db, ['email' => 'closeme@example.com']);
        $reach = Fixtures::reach($db, ['river' => 'R']);
        $cr = $this->seedCr($editor, $reach, ['reach' => ['description' => 'x']]);

        $before = count(glob(self::$mailDir . '/*') ?: []);
        $ok = review_reply_and_close($db, $cr, 'closing this out', $maint);
        $this->assertTrue($ok);
        $this->assertSame('resolved', $this->fetchCr($cr['id'])['status']);
        $after = count(glob(self::$mailDir . '/*') ?: []);
        $this->assertSame($before + 1, $after, 'one editor email dumped');
    }

    public function testReplyAndCloseRaceReturnsFalse(): void
    {
        $db = $this->pdo();
        $maint = Fixtures::editor($db, ['status' => 'maintainer']);
        $editor = Fixtures::editor($db);
        $reach = Fixtures::reach($db, ['river' => 'R']);
        $cr = $this->seedCr($editor, $reach, ['reach' => ['description' => 'x']], ['status' => 'rejected']);

        $this->assertFalse(review_reply_and_close($db, $cr, 'too late', $maint));
    }

    // -----------------------------------------------------------------------
    // review_send_reply — status preserved
    // -----------------------------------------------------------------------

    public function testSendReplyKeepsStatusAndEmailsEditor(): void
    {
        $db = $this->pdo();
        $maint = Fixtures::editor($db, ['status' => 'maintainer']);
        $editor = Fixtures::editor($db, ['email' => 'reply@example.com']);
        $reach = Fixtures::reach($db, ['river' => 'R']);
        $cr = $this->seedCr($editor, $reach, ['reach' => ['description' => 'x']]);

        $before = count(glob(self::$mailDir . '/*') ?: []);
        $this->assertTrue(review_send_reply($db, $cr, 'a question for you', $maint));

        $after = $this->fetchCr($cr['id']);
        $this->assertSame('pending', $after['status'], 'reply keeps the CR pending');
        $this->assertStringContainsString('a question for you', (string)$after['reviewer_note']);
        $this->assertSame(
            $before + 1,
            count(glob(self::$mailDir . '/*') ?: []),
            'reply emails the editor',
        );
    }

    public function testConcurrentRepliesBothSurvive(): void
    {
        // PR #119 review: replies don't flip status, so two reply tabs can
        // both pass the `pending` predicate. The append is SQL-side — the
        // second write must NOT drop the first reply's note even when made
        // from a stale request-start row (last-writer-wins regression).
        $db = $this->pdo();
        $maint = Fixtures::editor($db, ['status' => 'maintainer']);
        $editor = Fixtures::editor($db, ['email' => 'thread@example.com']);
        $reach = Fixtures::reach($db, ['river' => 'R']);
        $cr = $this->seedCr($editor, $reach, ['reach' => ['description' => 'x']]);

        $this->assertTrue(review_send_reply($db, $cr, 'first reply', $maint));
        // Tab B still holds the pre-first-reply row ($cr, reviewer_note null).
        $this->assertTrue(review_send_reply($db, $cr, 'second reply', $maint));

        $after = $this->fetchCr($cr['id']);
        $note = (string)$after['reviewer_note'];
        $this->assertStringContainsString('first reply', $note, 'first reply survives the race');
        $this->assertStringContainsString('second reply', $note);
        $this->assertSame('pending', $after['status']);
    }

    public function testSendReplyRaceDoesNotMutateTerminalRowOrEmail(): void
    {
        // Two maintainer tabs: tab A rejected the CR, tab B's stale "reply,
        // keep pending" submit must not append a note to the terminal row
        // nor email the editor a misleading "still pending" message.
        $db = $this->pdo();
        $maint = Fixtures::editor($db, ['status' => 'maintainer']);
        $editor = Fixtures::editor($db, ['email' => 'raced@example.com']);
        $reach = Fixtures::reach($db, ['river' => 'R']);
        $cr = $this->seedCr($editor, $reach, ['reach' => ['description' => 'x']], [
            'status' => 'rejected',
            'reviewer_note' => 'original decision note',
        ]);

        $before = count(glob(self::$mailDir . '/*') ?: []);
        $this->assertFalse(review_send_reply($db, $cr, 'too late', $maint));

        $after = $this->fetchCr($cr['id']);
        $this->assertSame('rejected', $after['status']);
        $this->assertSame('original decision note', $after['reviewer_note'], 'terminal note untouched');
        $this->assertSame($before, count(glob(self::$mailDir . '/*') ?: []), 'no email sent');
    }

    // -----------------------------------------------------------------------
    // review_notify_editor — with / without a deliverable address
    // -----------------------------------------------------------------------

    public function testNotifyEditorSendsWhenEmailPresent(): void
    {
        $db = $this->pdo();
        $editor = Fixtures::editor($db, ['email' => 'notify@example.com']);
        $reach = Fixtures::reach($db, ['river' => 'R']);
        $cr = $this->seedCr($editor, $reach, ['reach' => ['description' => 'x']], ['subject' => 'My subject']);

        $before = count(glob(self::$mailDir . '/*') ?: []);
        review_notify_editor($db, $cr, 'approved', 'congrats');
        $this->assertSame($before + 1, count(glob(self::$mailDir . '/*') ?: []));
    }

    public function testNotifyEditorNoEmailIsNoOp(): void
    {
        $db = $this->pdo();
        $editor = Fixtures::editor($db, ['email' => 'noemail@example.com']);
        $reach = Fixtures::reach($db, ['river' => 'R']);
        // No subject -> falls back to "reach #<id>" label (exercises that branch).
        $cr = $this->seedCr($editor, $reach, ['reach' => ['description' => 'x']], ['subject' => null]);

        // Point editor_id at a row that does not exist -> SELECT email misses,
        // function returns early (the $row === false branch).
        $cr['editor_id'] = 555555;
        $before = count(glob(self::$mailDir . '/*') ?: []);
        review_notify_editor($db, $cr, 'rejected', '');
        $this->assertSame($before, count(glob(self::$mailDir . '/*') ?: []), 'no email row -> no send');
    }

    // -----------------------------------------------------------------------
    // review_load_target_state
    // -----------------------------------------------------------------------

    public function testLoadTargetStateNonReachReturnsNull(): void
    {
        $this->assertNull(review_load_target_state($this->pdo(), 'gauge', 1));
    }

    public function testLoadTargetStateMissingReachReturnsNull(): void
    {
        $this->assertNull(review_load_target_state($this->pdo(), 'reach', 888888));
    }

    public function testLoadTargetStatePicksFirstPopulatedRange(): void
    {
        $db = $this->pdo();
        $reach = Fixtures::reach($db, ['river' => 'Range River', 'description' => 'd']);
        // First row has no bounds; second carries the range that should win.
        Fixtures::reachClass($db, $reach, ['name' => 'II']);
        Fixtures::reachClass($db, $reach, ['name' => 'III', 'low' => 250.0, 'low_data_type' => 'gauge', 'high' => 600.0, 'high_data_type' => 'gauge']);

        $state = review_load_target_state($db, 'reach', $reach);
        $this->assertNotNull($state);
        $this->assertSame(['II', 'III'], $state['reach_class']['names']);
        $this->assertEqualsWithDelta(250.0, (float)$state['reach_class']['range']['low'], 0.001);
        $this->assertSame('gauge', $state['reach_class']['range']['data_type'], 'data_type derived from low_data_type');
    }

    public function testLoadTargetStateNoClassesDefaultRange(): void
    {
        $db = $this->pdo();
        $reach = Fixtures::reach($db, ['river' => 'Bare River']);
        $state = review_load_target_state($db, 'reach', $reach);
        $this->assertNotNull($state);
        $this->assertSame([], $state['reach_class']['names']);
        $this->assertNull($state['reach_class']['range']['low']);
        $this->assertSame('flow', $state['reach_class']['range']['data_type']);
    }

    // -----------------------------------------------------------------------
    // merge_reviewer_note
    // -----------------------------------------------------------------------

    public function testMergeReviewerNoteBehaviors(): void
    {
        // Empty new note returns the prior thread unchanged.
        $this->assertSame('prev', merge_reviewer_note('prev', '   '));
        // First note (no prior thread) is just the stamped entry.
        $first = merge_reviewer_note('', 'hello');
        $this->assertStringContainsString('maintainer]', $first);
        $this->assertStringContainsString('hello', $first);
        $this->assertStringStartsWith('[', $first);
        // Subsequent note appends below the prior thread.
        $second = merge_reviewer_note($first, 'world');
        $this->assertStringContainsString('hello', $second);
        $this->assertStringContainsString('world', $second);
        $this->assertStringContainsString("\n\n", $second);
    }
}
