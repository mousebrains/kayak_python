<?php

declare(strict_types=1);

require_once __DIR__ . '/FunctionalTestCase.php';
require_once __DIR__ . '/Fixtures.php';
require_once __DIR__ . '/../../php/includes/review_logic.php';

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
    // review_approve — reach-column + reach_class mutation
    // -----------------------------------------------------------------------

    public function testApproveAppliesReachColumnsAndWritesHistory(): void
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

        $this->assertTrue($res['ok'], 'approve must succeed');

        // Reach columns updated.
        $st = $db->prepare('SELECT description, features, updated_at FROM reach WHERE id = ?');
        $st->execute([$reach]);
        $row = $st->fetch();
        $this->assertSame('brand new desc', $row['description']);
        $this->assertSame('a rapid', $row['features']);
        $this->assertNotNull($row['updated_at'], 'updated_at stamped');

        // change_request claimed: status -> approved, note merged, applied_json set.
        $crAfter = $this->fetchCr($cr['id']);
        $this->assertSame('approved', $crAfter['status']);
        $this->assertSame($maint, (int)$crAfter['reviewed_by']);
        $this->assertNotNull($crAfter['reviewed_at']);
        $this->assertStringContainsString('looks good', (string)$crAfter['reviewer_note']);
        $this->assertStringContainsString('maintainer]', (string)$crAfter['reviewer_note']);
        $applied_json = json_decode((string)$crAfter['applied_json'], true);
        $this->assertSame('brand new desc', $applied_json['reach']['description']);

        // One edit_history row per changed field, with old/new values + actor.
        $st = $db->prepare(
            'SELECT field, old_value, new_value, changed_by FROM edit_history
             WHERE change_request_id = ? ORDER BY field'
        );
        $st->execute([$cr['id']]);
        $hist = $st->fetchAll();
        $this->assertCount(2, $hist);
        $byField = array_column($hist, null, 'field');
        $this->assertSame('old desc', $byField['description']['old_value']);
        $this->assertSame('brand new desc', $byField['description']['new_value']);
        $this->assertSame('maintainer:' . $maint, $byField['description']['changed_by']);
        // features had no prior value -> old_value NULL.
        $this->assertNull($byField['features']['old_value']);
    }

    public function testApproveBlankValueStoredAsNull(): void
    {
        $db = $this->pdo();
        $maint = Fixtures::editor($db, ['status' => 'maintainer']);
        $editor = Fixtures::editor($db);
        $reach = Fixtures::reach($db, ['description' => 'something', 'river' => 'R']);
        $cr = $this->seedCr($editor, $reach, ['reach' => ['description' => '']]);

        // Empty-string overlay collapses to NULL in the reach column.
        $res = review_approve($db, $cr, ['reach' => ['description' => '']], $maint, '');
        $this->assertTrue($res['ok']);

        $st = $db->prepare('SELECT description FROM reach WHERE id = ?');
        $st->execute([$reach]);
        $this->assertNull($st->fetchColumn());
    }

    public function testApproveDropsNonAllowlistedReachField(): void
    {
        $db = $this->pdo();
        $maint = Fixtures::editor($db, ['status' => 'maintainer']);
        $editor = Fixtures::editor($db);
        $reach = Fixtures::reach($db, ['description' => 'old desc', 'river' => 'R']);

        // Capture a real reach column the proposal must NOT be able to set.
        $st = $db->prepare('SELECT no_show FROM reach WHERE id = ?');
        $st->execute([$reach]);
        $noShowBefore = $st->fetchColumn();

        // Tampered payload_json: a legit field plus two non-proposable reach columns.
        // R1.4: the apply path interpolates the column name as a SQL identifier, so it
        // intersects payload keys against the proposable-fields allowlist. The forged
        // keys are dropped (not written); the legit field applies and approve succeeds.
        $applied = ['reach' => [
            'description' => 'new desc',  // allowlisted -> applied
            'no_show'     => 1,           // real column, not proposable -> dropped
            'id'          => 999999,      // dropped
        ]];
        $cr = $this->seedCr($editor, $reach, $applied);

        $res = review_approve($db, $cr, $applied, $maint, '');
        $this->assertTrue($res['ok'], 'forged keys are dropped, not fatal');

        $st = $db->prepare('SELECT id, description, no_show FROM reach WHERE id = ?');
        $st->execute([$reach]);
        $row = $st->fetch();
        $this->assertSame('new desc', $row['description'], 'allowlisted field applied');
        $this->assertSame($noShowBefore, $row['no_show'], 'non-proposable no_show untouched');
        $this->assertSame($reach, (int)$row['id'], 'forged id ignored');

        // edit_history only for the allowlisted field.
        $st = $db->prepare('SELECT field FROM edit_history WHERE change_request_id = ?');
        $st->execute([$cr['id']]);
        $this->assertSame(['description'], array_column($st->fetchAll(), 'field'));
    }

    public function testApproveReplacesReachClassSetAndLogsDiff(): void
    {
        $db = $this->pdo();
        $maint = Fixtures::editor($db, ['status' => 'maintainer']);
        $editor = Fixtures::editor($db);
        $reach = Fixtures::reach($db, ['river' => 'Class River']);
        // Pre-existing classes that should be wiped and replaced.
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

        $st = $db->prepare('SELECT name, low, high, low_data_type FROM reach_class WHERE reach_id = ? ORDER BY id');
        $st->execute([$reach]);
        $rows = $st->fetchAll();
        $this->assertSame(['III', 'III+', 'IV'], array_column($rows, 'name'));
        // Shared range applied to every row.
        $this->assertEqualsWithDelta(300.0, (float)$rows[0]['low'], 0.001);
        $this->assertEqualsWithDelta(800.0, (float)$rows[0]['high'], 0.001);
        $this->assertSame('flow', $rows[2]['low_data_type']);

        // A single reach_class edit_history row carrying the JSON diff.
        $st = $db->prepare("SELECT old_value, new_value FROM edit_history WHERE change_request_id = ? AND field = 'reach_class'");
        $st->execute([$cr['id']]);
        $h = $st->fetch();
        $this->assertNotFalse($h);
        $this->assertStringContainsString('"II"', (string)$h['old_value']);
        $this->assertStringContainsString('"III+"', (string)$h['new_value']);
    }

    public function testApproveSkipsReachClassWhenUnchanged(): void
    {
        $db = $this->pdo();
        $maint = Fixtures::editor($db, ['status' => 'maintainer']);
        $editor = Fixtures::editor($db);
        $reach = Fixtures::reach($db, ['river' => 'NoChange River']);
        Fixtures::reachClass($db, $reach, ['name' => 'III', 'low' => 500.0, 'low_data_type' => 'flow', 'high' => 1000.0, 'high_data_type' => 'flow']);

        // applied reach_class identical to current -> the $old_dump===$new_dump
        // guard short-circuits: no DELETE/INSERT, no edit_history row.
        $applied = [
            'reach' => [],
            'reach_class' => [
                'names' => ['III'],
                'range' => ['low' => 500.0, 'high' => 1000.0, 'data_type' => 'flow'],
            ],
        ];
        $cr = $this->seedCr($editor, $reach, $applied);

        $res = review_approve($db, $cr, $applied, $maint, '');
        $this->assertTrue($res['ok']);
        $this->assertSame(0, $this->historyCount($cr['id']), 'unchanged reach_class writes no history');
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
        // CR untouched (no transaction even started).
        $this->assertSame('pending', $this->fetchCr($cr['id'])['status']);
    }

    public function testApproveCaughtExceptionRollsBackAndReportsGenericError(): void
    {
        $db = $this->pdo();
        $maint = Fixtures::editor($db, ['status' => 'maintainer']);
        $editor = Fixtures::editor($db);
        $reach = Fixtures::reach($db, ['description' => 'keep me', 'river' => 'R']);
        // A reach_class range with low > high violates ck_reach_class_low_le_high,
        // so the INSERT throws inside the try -> rollback. (A bogus reach *column*
        // no longer reaches the UPDATE — the R1.4 allowlist drops it — so the
        // catch/rollback path is exercised via the reach_class CHECK instead.)
        $applied = [
            'reach'       => ['description' => 'should not persist'],
            'reach_class' => [
                'names' => ['III'],
                'range' => ['low' => 900.0, 'high' => 100.0, 'data_type' => 'flow'],
            ],
        ];
        $cr = $this->seedCr($editor, $reach, $applied);

        $res = review_approve($db, $cr, $applied, $maint, 'note');
        $this->assertFalse($res['ok']);
        $this->assertStringContainsString('apply failed', (string)$res['err']);

        // Rolled back: status pending, the reach UPDATE reverted, no classes, no history.
        $this->assertSame('pending', $this->fetchCr($cr['id'])['status']);
        $st = $db->prepare('SELECT description FROM reach WHERE id = ?');
        $st->execute([$reach]);
        $this->assertSame('keep me', $st->fetchColumn());
        $st = $db->prepare('SELECT COUNT(*) FROM reach_class WHERE reach_id = ?');
        $st->execute([$reach]);
        $this->assertSame(0, (int)$st->fetchColumn());
        $this->assertSame(0, $this->historyCount($cr['id']));
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
        review_send_reply($db, $cr, 'a question for you', $maint);

        $after = $this->fetchCr($cr['id']);
        $this->assertSame('pending', $after['status'], 'reply keeps the CR pending');
        $this->assertStringContainsString('a question for you', (string)$after['reviewer_note']);
        $this->assertSame(
            $before + 1,
            count(glob(self::$mailDir . '/*') ?: []),
            'reply emails the editor',
        );
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
