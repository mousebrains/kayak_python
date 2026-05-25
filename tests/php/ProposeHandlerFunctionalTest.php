<?php

declare(strict_types=1);

require_once __DIR__ . '/FunctionalTestCase.php';
require_once __DIR__ . '/Fixtures.php';
require_once __DIR__ . '/../../php/includes/db.php';
require_once __DIR__ . '/../../php/includes/auth.php';
require_once __DIR__ . '/../../php/includes/header.php';
require_once __DIR__ . '/../../php/includes/footer.php';
require_once __DIR__ . '/../../php/includes/propose_handler.php';

/**
 * In-process functional coverage for propose_handler.php — the editor
 * proposal form + POST validate/upsert flow.
 *
 * The HTTP/CSRF/session/feature-flag gating (anonymous redirect, maintainer
 * skip-to-/edit, invalid-CSRF 403) lives in ProposeIntegrationTest, which
 * drives the real `php -S` subprocess. This class calls handle_propose()
 * (and _handle_propose_post via it) directly so pcov counts the validation,
 * diff-only payload build, upsert, prefill, and render branches.
 *
 * CSRF is satisfied by setting a matching ed_csrf cookie + csrf_token POST
 * field (double-submit), since require_csrf() lives in auth.php (not one of
 * the seam-converted files) and still bare-exits on mismatch.
 *
 * Email is routed to MAIL_DUMP_DIR; MAINTAINER_EMAIL is set so the
 * maintainer-notification branch in _send_proposal_notification actually
 * runs (and lands a file we can count).
 */
final class ProposeHandlerFunctionalTest extends FunctionalTestCase
{
    private const CSRF = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';

    private static int $reachId = 0;
    private static string $mailDir = '';

    public static function setUpBeforeClass(): void
    {
        self::$mailDir = sys_get_temp_dir() . '/kayak-propose-mail-' . getmypid();
        if (!is_dir(self::$mailDir)) {
            mkdir(self::$mailDir, 0700, true);
        }
        putenv('MAIL_DUMP_DIR=' . self::$mailDir);
        putenv('MAINTAINER_EMAIL=maint@example.com');
        Config::install_for_tests(['mail_dump_dir' => self::$mailDir, 'site_url' => 'https://levels.test']);
        parent::setUpBeforeClass();
    }

    public static function tearDownAfterClass(): void
    {
        parent::tearDownAfterClass();
        Config::install_for_tests([]);
        putenv('MAIL_DUMP_DIR');
        putenv('MAINTAINER_EMAIL');
        if (self::$mailDir !== '' && is_dir(self::$mailDir)) {
            array_map('unlink', glob(self::$mailDir . '/*') ?: []);
            @rmdir(self::$mailDir);
        }
        self::$mailDir = '';
    }

    protected static function seedDatabase(PDO $db): void
    {
        self::$reachId = Fixtures::reach($db, [
            'name' => 'Propose Reach',
            'display_name' => 'Propose Reach',
            'river' => 'Propose River',
            'description' => 'Original description.',
            'latitude' => 44.0,
            'longitude' => -122.0,
        ]);
        Fixtures::reachClass($db, self::$reachId, ['name' => 'III', 'low' => 400.0, 'low_data_type' => 'flow', 'high' => 900.0, 'high_data_type' => 'flow']);
    }

    /** A signed-in editor row of the given tier (only ['id','status','email'] are read). */
    private function ed(string $status = 'full', ?int $id = null): array
    {
        $db = $this->pdo();
        $id ??= Fixtures::editor($db, ['status' => $status]);
        return ['id' => $id, 'status' => $status, 'email' => "ed$id@example.com"];
    }

    /** Make the request look like a valid double-submit-CSRF POST. */
    private function asPost(array $fields): void
    {
        $_SERVER['REQUEST_METHOD'] = 'POST';
        $_COOKIE[EDITOR_CSRF_COOKIE] = self::CSRF;
        $_POST = $fields + ['csrf_token' => self::CSRF];
    }

    private function pendingCrCount(int $editorId): int
    {
        $st = $this->pdo()->prepare(
            "SELECT COUNT(*) FROM change_request WHERE editor_id = ? AND status = 'pending'"
        );
        $st->execute([$editorId]);
        return (int)$st->fetchColumn();
    }

    private function latestCr(int $editorId): ?array
    {
        $st = $this->pdo()->prepare(
            'SELECT * FROM change_request WHERE editor_id = ? ORDER BY id DESC LIMIT 1'
        );
        $st->execute([$editorId]);
        $row = $st->fetch();
        return $row === false ? null : $row;
    }

    // -----------------------------------------------------------------------
    // handle_propose dispatch guards (now via the http_terminate seam)
    // -----------------------------------------------------------------------

    public function testNonReachTypeIs400(): void
    {
        $e = $this->captureExit(fn() => handle_propose($this->pdo(), $this->ed(), 'gauge', self::$reachId));
        $this->assertSame(400, $e->statusCode);
        $this->assertStringContainsString('reach proposals', $e->getMessage());
    }

    public function testMissingIdIs400(): void
    {
        $e = $this->captureExit(fn() => handle_propose($this->pdo(), $this->ed(), 'reach', 0));
        $this->assertSame(400, $e->statusCode);
    }

    public function testUnknownReachIs404(): void
    {
        // get_reach_or_404 renders the rich HTML 404 via render_error_page,
        // which terminates through the same seam.
        $e = $this->captureExit(fn() => handle_propose($this->pdo(), $this->ed(), 'reach', 777777));
        $this->assertSame(404, $e->statusCode);
    }

    // -----------------------------------------------------------------------
    // GET render — form, tier gating, prefill
    // -----------------------------------------------------------------------

    public function testGetRendersFormForFullEditor(): void
    {
        $html = $this->capture(fn() => handle_propose($this->pdo(), $this->ed('full'), 'reach', self::$reachId));
        $this->assertStringContainsString('Suggest an edit', $html);
        $this->assertStringContainsString('Propose Reach', $html);
        // Full tier unlocks the display_name + classes + put-in/take-out blocks.
        $this->assertStringContainsString('name="display_name"', $html);
        $this->assertStringContainsString('name="classes"', $html);
        $this->assertStringContainsString('name="latitude_start"', $html);
        // Current class prefilled into the classes input.
        $this->assertStringContainsString('value="III"', $html);
    }

    public function testGetMinimalTierHidesFullOnlyFields(): void
    {
        $html = $this->capture(fn() => handle_propose($this->pdo(), $this->ed('minimal'), 'reach', self::$reachId));
        $this->assertStringContainsString('name="description"', $html);
        // minimal tier: only text fields, no display_name / coordinate / class widgets.
        $this->assertStringNotContainsString('name="display_name"', $html);
        $this->assertStringNotContainsString('name="latitude_start"', $html);
    }

    // -----------------------------------------------------------------------
    // POST — successful save (new) + maintainer notification
    // -----------------------------------------------------------------------

    public function testPostNewProposalCreatesChangeRequestAndNotifies(): void
    {
        $ed = $this->ed('full');
        $mailBefore = count(glob(self::$mailDir . '/*') ?: []);
        $this->asPost([
            'description' => 'A freshly proposed description.',
            'features' => 'New surf wave',
            'notes_to_maint' => 'spotted on a trip',
            'source_url' => '/description.php?id=' . self::$reachId,
        ]);

        $html = $this->capture(fn() => handle_propose($this->pdo(), $ed, 'reach', self::$reachId));
        $this->assertStringContainsString('your proposal was recorded', $html);

        $this->assertSame(1, $this->pendingCrCount($ed['id']));
        $cr = $this->latestCr($ed['id']);
        $this->assertNotNull($cr);
        $payload = json_decode((string)$cr['payload_json'], true);
        $this->assertSame('A freshly proposed description.', $payload['reach']['description']);
        $this->assertSame('New surf wave', $payload['reach']['features']);
        $this->assertSame('spotted on a trip', $cr['notes_to_maint']);
        $this->assertSame('/description.php?id=' . self::$reachId, $cr['source_url']);

        // Maintainer was notified (MAINTAINER_EMAIL set -> one dumped file).
        $this->assertGreaterThan($mailBefore, count(glob(self::$mailDir . '/*') ?: []));
    }

    public function testPostUpdatesExistingPendingProposalInPlace(): void
    {
        $ed = $this->ed('full');
        // First submission.
        $this->asPost(['description' => 'first version']);
        $this->capture(fn() => handle_propose($this->pdo(), $ed, 'reach', self::$reachId));
        $this->assertSame(1, $this->pendingCrCount($ed['id']));
        $firstId = $this->latestCr($ed['id'])['id'];

        // Second submission for the same reach updates the same row (no stacking).
        $this->asPost(['description' => 'second version']);
        $this->capture(fn() => handle_propose($this->pdo(), $ed, 'reach', self::$reachId));
        $this->assertSame(1, $this->pendingCrCount($ed['id']), 'still a single pending CR');
        $cr = $this->latestCr($ed['id']);
        $this->assertSame($firstId, $cr['id'], 'same row updated');
        $payload = json_decode((string)$cr['payload_json'], true);
        $this->assertSame('second version', $payload['reach']['description']);
    }

    public function testPostClassAndCoordinateChangesLandInPayload(): void
    {
        $ed = $this->ed('full');
        $this->asPost([
            'classes_present' => '1',
            'classes' => 'IV, IV+',
            'flow_low' => '500',
            'flow_high' => '1200',
            'flow_data_type' => 'flow',
            'latitude_start' => '44.10',
            'longitude_start' => '-122.10',
            'latitude_end' => '44.05',
            'longitude_end' => '-122.05',
        ]);
        $html = $this->capture(fn() => handle_propose($this->pdo(), $ed, 'reach', self::$reachId));
        $this->assertStringContainsString('your proposal was recorded', $html);

        $payload = json_decode((string)$this->latestCr($ed['id'])['payload_json'], true);
        $this->assertSame(['IV', 'IV+'], $payload['reach_class']['names']);
        $this->assertEqualsWithDelta(500.0, $payload['reach_class']['range']['low'], 0.001);
        $this->assertArrayHasKey('latitude_start', $payload['reach']);
        $this->assertEqualsWithDelta(44.10, $payload['reach']['latitude_start'], 0.001);
    }

    // -----------------------------------------------------------------------
    // POST — validation + no-op branches (no row written)
    // -----------------------------------------------------------------------

    public function testPostNonNumericCoordinateProducesNoSavedRow(): void
    {
        // Exercises the `is_numeric() ? : false` rejection branch for a
        // coordinate field. NB: the inline `$errors[] = '… must be a number.'`
        // there is later overwritten by the `$errors = …sanity_errors(…)`
        // reassignment, so the format message never reaches the page; the
        // field is simply dropped from the diff. The net effect (no row
        // written) is what we assert — the branch still executes for coverage.
        $ed = $this->ed('full');
        $this->asPost(['latitude_start' => 'not-a-number']);
        $html = $this->capture(fn() => handle_propose($this->pdo(), $ed, 'reach', self::$reachId));
        $this->assertStringNotContainsString('your proposal was recorded', $html);
        $this->assertSame(0, $this->pendingCrCount($ed['id']), 'rejected coordinate yields no CR');
    }

    public function testPostDisplayNameMissingRiverIsError(): void
    {
        $ed = $this->ed('full');
        // Display name without the river token trips check_display_name.
        $this->asPost(['display_name' => 'Some Unrelated Run']);
        $html = $this->capture(fn() => handle_propose($this->pdo(), $ed, 'reach', self::$reachId));
        $this->assertStringContainsString('must include the river name', $html);
        $this->assertSame(0, $this->pendingCrCount($ed['id']));
    }

    public function testPostNoChangesAndNoNoteIsRejected(): void
    {
        $ed = $this->ed('full');
        // Resubmit the current values verbatim + empty note -> "No changes detected".
        $this->asPost([
            'description' => 'Original description.',
            'display_name' => 'Propose Reach',
            'notes_to_maint' => '',
        ]);
        $html = $this->capture(fn() => handle_propose($this->pdo(), $ed, 'reach', self::$reachId));
        $this->assertStringContainsString('No changes detected', $html);
        $this->assertSame(0, $this->pendingCrCount($ed['id']));
    }

    public function testPostNotesOnlyIsAccepted(): void
    {
        $ed = $this->ed('full');
        // No field changes but a note -> CR with empty payload is still created.
        $this->asPost([
            'description' => 'Original description.',
            'display_name' => 'Propose Reach',
            'notes_to_maint' => 'just flagging this reach for a look',
        ]);
        $html = $this->capture(fn() => handle_propose($this->pdo(), $ed, 'reach', self::$reachId));
        $this->assertStringContainsString('your proposal was recorded', $html);
        $cr = $this->latestCr($ed['id']);
        $this->assertSame('[]', $cr['payload_json'], 'empty diff payload');
        $this->assertSame('just flagging this reach for a look', $cr['notes_to_maint']);
    }

    public function testHoneypotSilentlyAcceptsWithoutWriting(): void
    {
        $ed = $this->ed('full');
        $this->asPost([
            'website' => 'http://spam.example', // honeypot filled
            'description' => 'spammy text',
        ]);
        $html = $this->capture(fn() => handle_propose($this->pdo(), $ed, 'reach', self::$reachId));
        $this->assertStringContainsString('your proposal was recorded', $html, 'bot sees success');
        $this->assertSame(0, $this->pendingCrCount($ed['id']), 'but nothing is stored');
    }

    public function testGetWithExistingPendingProposalPrefillsAndNotices(): void
    {
        $db = $this->pdo();
        $ed = $this->ed('full');
        // Seed an existing pending CR for this editor+reach so the GET render
        // hits the "you already have a pending proposal" notice + _propose_prefill
        // pulls the description from the stored payload_json.
        Fixtures::changeRequest($db, $ed['id'], [
            'target_id' => self::$reachId,
            'payload_json' => json_encode(['reach' => ['description' => 'previously proposed text']]),
            'subject' => 'Proposed edit: Propose Reach',
        ]);
        $html = $this->capture(fn() => handle_propose($db, $ed, 'reach', self::$reachId));
        $this->assertStringContainsString('already have a pending proposal', $html);
        $this->assertStringContainsString('previously proposed text', $html, 'prefill from existing payload');
    }

    public function testPostFarCoordinateProducesWarningButStillSaves(): void
    {
        $ed = $this->ed('full');
        // ~10+ mi from the reach's (44.0, -122.0) ref -> check_coords warning
        // (not an error), so _handle_propose_post returns warnings AND still
        // saves; the success banner replaces the form (warnings ride along in
        // the maintainer's view). Exercises the warnings-present save path.
        $this->asPost([
            'latitude_start' => '44.30',
            'longitude_start' => '-122.05',
            'latitude_end' => '44.31',
            'longitude_end' => '-122.06',
        ]);
        $html = $this->capture(fn() => handle_propose($this->pdo(), $ed, 'reach', self::$reachId));
        $this->assertStringContainsString('your proposal was recorded', $html);
        $payload = json_decode((string)$this->latestCr($ed['id'])['payload_json'], true);
        $this->assertArrayHasKey('latitude_start', $payload['reach']);
    }

    public function testGetReRendersWarningsBlockOnValidationError(): void
    {
        $db = $this->pdo();
        $ed = $this->ed('full');
        // A pre-existing pending CR (so we're past the cap guard) + a POST that
        // pairs a hard error (display name w/o river) with a far-coordinate
        // warning. errors!=[] re-renders the form, exercising BOTH the red
        // errors <ul> and the yellow warnings <ul> + the existing-proposal
        // notice in the same render.
        Fixtures::changeRequest($db, $ed['id'], [
            'target_id' => self::$reachId,
            'payload_json' => json_encode(['reach' => ['description' => 'prior']]),
            'subject' => 'Proposed edit: Propose Reach',
        ]);
        $this->asPost([
            'display_name' => 'Unrelated Run',          // hard error (missing river)
            'latitude_start' => '44.30',                 // far -> warning
            'longitude_start' => '-122.05',
        ]);
        $html = $this->capture(fn() => handle_propose($db, $ed, 'reach', self::$reachId));
        $this->assertStringContainsString('must include the river name', $html);   // error ul
        $this->assertStringContainsString('from the current location', $html);      // warning ul
        $this->assertStringContainsString('already have a pending proposal', $html);
    }

    public function testPostClassRangeClearedSavesNullRange(): void
    {
        $ed = $this->ed('full');
        // classes_present with empty low+high -> reach_class payload with a
        // (null,null) range, exercising the '(no range)' branch of the
        // notification's range formatter in _send_proposal_notification.
        $this->asPost([
            'classes_present' => '1',
            'classes' => 'IV',
            'flow_low' => '',
            'flow_high' => '',
            'flow_data_type' => 'flow',
        ]);
        $html = $this->capture(fn() => handle_propose($this->pdo(), $ed, 'reach', self::$reachId));
        $this->assertStringContainsString('your proposal was recorded', $html);
        $payload = json_decode((string)$this->latestCr($ed['id'])['payload_json'], true);
        $this->assertNull($payload['reach_class']['range']['low']);
        $this->assertNull($payload['reach_class']['range']['high']);
    }

    public function testGetNameFallbackWhenNoDisplayName(): void
    {
        $db = $this->pdo();
        // Reach with name but no display_name -> reach_name falls back to name.
        $rid = Fixtures::reach($db, ['name' => 'Fallback Run', 'display_name' => null, 'river' => 'Fallback River']);
        $html = $this->capture(fn() => handle_propose($db, $this->ed('full'), 'reach', $rid));
        $this->assertStringContainsString('Fallback Run', $html);
    }

    public function testDailyCapBlocksNewProposal(): void
    {
        $db = $this->pdo();
        // A fresh "pending"-tier editor has a cap of 3.
        $ed = $this->ed('pending');
        for ($i = 0; $i < 3; $i++) {
            Fixtures::changeRequest($db, $ed['id'], [
                'target_id' => self::$reachId,
                'subject' => "earlier $i",
                'payload_json' => '{}',
                'status' => 'rejected', // counts toward the daily window, not "existing pending"
            ]);
        }
        $this->asPost(['description' => 'one more please']);
        $html = $this->capture(fn() => handle_propose($db, $ed, 'reach', self::$reachId));
        $this->assertStringContainsString('Daily submission cap', $html);
        $this->assertSame(0, $this->pendingCrCount($ed['id']));
    }
}
