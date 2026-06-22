<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

/**
 * Regression: two maintainers (or two browser tabs) submitting the approve
 * form for the same change_request must not both apply changes — the second
 * call must detect that the row's already-reviewed and bail without writing
 * to reach / edit_history / change_request a second time.
 *
 * The row's status itself acts as the lock: review_approve runs a conditional
 * UPDATE (`WHERE id=? AND status='pending'`) up front; rowCount==0 ->
 * rollback the transaction, return ok=false.
 */
final class ReviewApproveRaceTest extends TestCase
{
    public static function setUpBeforeClass(): void
    {
        // Route any send_email() calls to a temp dir so mail.php doesn't
        // try to invoke the real mail() during the test.
        $dump = sys_get_temp_dir() . '/kayak-test-mail-' . getmypid();
        if (!is_dir($dump)) mkdir($dump, 0700, true);
        putenv("MAIL_DUMP_DIR=$dump");
        require_once __DIR__ . '/../../src/kayak/web/php/includes/review_logic.php';
    }

    private function pdo(): PDO
    {
        $pdo = new PDO('sqlite::memory:');
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $pdo->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);

        $pdo->exec("
            CREATE TABLE editor (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            );
            CREATE TABLE reach (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                description TEXT,
                updated_at DATETIME
            );
            CREATE TABLE reach_class (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reach_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                low REAL, low_data_type TEXT,
                high REAL, high_data_type TEXT
            );
            CREATE TABLE change_request (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_type TEXT NOT NULL,
                target_id INTEGER,
                editor_id INTEGER NOT NULL,
                submitted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                subject TEXT,
                payload_json TEXT NOT NULL,
                notes_to_maint TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                reviewed_at DATETIME,
                reviewed_by INTEGER,
                reviewer_note TEXT,
                applied_json TEXT,
                source_url TEXT
            );
            CREATE TABLE edit_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_type TEXT NOT NULL,
                target_id INTEGER,
                change_request_id INTEGER,
                field TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                changed_by TEXT NOT NULL
            );
            -- Minimal shape for the bridge-row enqueue: UNIQUE(change_request_id)
            -- is required for ON CONFLICT. The production FK/CASCADE invariants
            -- (ON DELETE CASCADE, queued_by FK) are pinned by the real init-db
            -- schema in EditorBridgeFunctionalTest + the integration suites, not
            -- here (this hand-rolled schema doesn't enforce FKs).
            CREATE TABLE change_request_bridge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                change_request_id INTEGER NOT NULL UNIQUE,
                state TEXT NOT NULL DEFAULT 'queued',
                attempt INTEGER NOT NULL DEFAULT 1,
                base_dataset_sha TEXT,
                reviewed_base_json TEXT,
                applied_json_sha256 TEXT,
                queued_by INTEGER,
                queued_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        ");
        return $pdo;
    }

    private function seed(PDO $db): array
    {
        $db->exec("INSERT INTO editor (email, status) VALUES ('m@example.com', 'maintainer')");
        $maint_id = (int)$db->lastInsertId();
        $db->exec("INSERT INTO editor (email) VALUES ('e@example.com')");
        $editor_id = (int)$db->lastInsertId();

        $db->exec("INSERT INTO reach (name, description) VALUES ('Old Name', 'old desc')");
        $reach_id = (int)$db->lastInsertId();

        // 'name' is the internal reach identifier, not a proposable field — the
        // R1.4 apply-path allowlist drops it — so the race fixtures change
        // 'description', which editors can actually propose.
        $payload = ['reach' => ['description' => 'new desc']];
        $st = $db->prepare(
            "INSERT INTO change_request (target_type, target_id, editor_id, payload_json, status)
             VALUES ('reach', ?, ?, ?, 'pending')"
        );
        $st->execute([$reach_id, $editor_id, json_encode($payload)]);
        $cr_id = (int)$db->lastInsertId();

        $cr_stmt = $db->prepare('SELECT * FROM change_request WHERE id = ?');
        $cr_stmt->execute([$cr_id]);
        $cr = $cr_stmt->fetch();

        return ['maint_id' => $maint_id, 'reach_id' => $reach_id, 'cr_id' => $cr_id, 'cr' => $cr];
    }

    public function testDoubleApproveBailsOnSecondCall(): void
    {
        $db = $this->pdo();
        $seed = $this->seed($db);
        $applied = ['reach' => ['description' => 'new desc']];

        // First endorse succeeds and freezes the diff (SA-lite: the reach row
        // itself is never written — the dataset repo is the metadata authority).
        $r1 = review_approve($db, $seed['cr'], $applied, $seed['maint_id'], 'lgtm');
        $this->assertTrue($r1['ok'], 'first approve must succeed');

        $reach = $db->query('SELECT description FROM reach WHERE id = ' . $seed['reach_id'])->fetch();
        $this->assertNotSame('new desc', $reach['description'], 'endorse must not write the reach');

        $frozen = (string)$db->query(
            'SELECT applied_json FROM change_request WHERE id = ' . $seed['cr_id']
        )->fetchColumn();
        $this->assertStringContainsString('new desc', $frozen, 'first approve freezes the diff');

        // Second approval (using a stale $cr that still says 'pending', which
        // is exactly the state both browser tabs would have read) must lose
        // the CAS claim and report the row was already reviewed.
        $r2 = review_approve($db, $seed['cr'], $applied, $seed['maint_id'], 'me too');
        $this->assertFalse($r2['ok'], 'second approve must NOT succeed');
        $this->assertStringContainsString('Already reviewed', (string)$r2['err']);

        // Still zero audit rows: nothing is ever applied on endorse.
        $history_after = (int)$db->query(
            'SELECT COUNT(*) FROM edit_history WHERE change_request_id = ' . $seed['cr_id']
        )->fetchColumn();
        $this->assertSame(0, $history_after, 'endorse writes no edit_history');

        // change_request still shows the FIRST maintainer's note, not "me too"
        $note = (string)$db->query(
            'SELECT reviewer_note FROM change_request WHERE id = ' . $seed['cr_id']
        )->fetchColumn();
        $this->assertStringContainsString('lgtm', $note);
        $this->assertStringNotContainsString('me too', $note);

        // Tier 2: the first endorse queued exactly ONE bridge row; the second
        // (lost CAS) rolled back before reaching the queue insert, so no
        // duplicate. The bridgeable reach diff captured its reviewed base.
        $bridge = $db->query(
            'SELECT change_request_id, state, queued_by, reviewed_base_json
             FROM change_request_bridge WHERE change_request_id = ' . $seed['cr_id']
        )->fetchAll();
        $this->assertCount(1, $bridge, 'exactly one bridge row after a double-approve');
        $this->assertSame('queued', $bridge[0]['state']);
        $this->assertSame($seed['maint_id'], (int)$bridge[0]['queued_by']);
        $base = json_decode((string)$bridge[0]['reviewed_base_json'], true);
        $this->assertSame('old desc', $base['reach']['description'], 'captured the pre-edit base');
    }

    public function testRejectAfterApproveBails(): void
    {
        $db = $this->pdo();
        $seed = $this->seed($db);
        $applied = ['reach' => ['description' => 'new desc']];

        $r1 = review_approve($db, $seed['cr'], $applied, $seed['maint_id'], '');
        $this->assertTrue($r1['ok']);

        // Asymmetric race: one approve, one reject. The reject must lose
        // since the row is no longer 'pending'.
        $r2 = review_reject($db, $seed['cr'], 'no actually', $seed['maint_id']);
        $this->assertFalse($r2, 'reject must not transition an already-approved row');

        $status = (string)$db->query(
            'SELECT status FROM change_request WHERE id = ' . $seed['cr_id']
        )->fetchColumn();
        $this->assertSame('approved', $status);
    }
}
