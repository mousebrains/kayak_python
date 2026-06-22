<?php
declare(strict_types=1);
/**
 * Pure logic for the change_request review flow — extracted from review.php
 * so unit tests can exercise approve/reject/resolve/reply without pulling in
 * the page-rendering controller.
 *
 * The `$cr` argument threaded through every review_* helper is a
 * `SELECT * FROM change_request` row, shaped against the dev SQLite schema
 * (PDO, EMULATE_PREPARES=false): INTEGER→int, TEXT/VARCHAR/DATETIME→string,
 * `|null` for each notnull=0 column. It is inlined on each @param below
 * (this project's PHPStan run does not register @phpstan-type aliases —
 * see the same note in propose_handler.php). The matching shape lives at
 * the fetch sites in review_handler.php / propose_handler.php:
 *   array{id: int, target_type: string, target_id: int|null, editor_id: int,
 *     submitted_at: string, subject: string|null, payload_json: string,
 *     notes_to_maint: string|null, status: string, reviewed_at: string|null,
 *     reviewed_by: int|null, reviewer_note: string|null, applied_json: string|null,
 *     source_url: string|null}
 */

require_once __DIR__ . '/mail.php';
require_once __DIR__ . '/reach_propose_fields.php';
require_once __DIR__ . '/editor_bridge.php';

// ---------------------------------------------------------------------------
// Apply helpers
// ---------------------------------------------------------------------------

/**
 * Load the current state for a change_request's target so we can diff
 * and later build edit_history rows. Returns associative state arrays
 * keyed by 'reach', 'reach_class' (names + primary range).
 *
 * @return array{reach: array<string, mixed>, reach_class: array{names: list<string>, range: array{low: ?float, high: ?float, data_type: string}}}|null
 */
function review_load_target_state(PDO $db, string $type, int $id): ?array {
    if ($type !== 'reach') return null;
    $st = $db->prepare('SELECT * FROM reach WHERE id = ?');
    $st->execute([$id]);
    /** @var array{id: int, updated_at: string|null, gauge_id: int|null, name: string|null, display_name: string|null, sort_name: string|null, nature: string|null, description: string|null, difficulties: string|null, basin: string|null, basin_area: float|null, elevation: float|null, elevation_lost: float|null, length: float|null, gradient: float|null, features: string|null, latitude: float|null, longitude: float|null, latitude_start: float|null, longitude_start: float|null, latitude_end: float|null, longitude_end: float|null, no_show: int, notes: string|null, optimal_flow: float|null, region: string|null, remoteness: string|null, scenery: string|null, season: string|null, watershed_type: string|null, aw_id: int|null, river: string|null, max_gradient: float|null, geom: string|null, huc: string|null, map_only: int, no_flow_range: int, gradient_profile: string|null, gradient_unreliable: int}|false $reach */
    $reach = $st->fetch();
    if ($reach === false) return null;

    $st = $db->prepare(
        'SELECT name, low, low_data_type, high, high_data_type
         FROM reach_class WHERE reach_id = ? ORDER BY id'
    );
    $st->execute([$id]);
    /** @var list<array{name: string, low: float|null, low_data_type: string|null, high: float|null, high_data_type: string|null}> $rows */
    $rows = $st->fetchAll();
    $classes = array_column($rows, 'name');
    $range = ['low' => null, 'high' => null, 'data_type' => 'flow'];
    foreach ($rows as $row) {
        if ($row['low'] !== null || $row['high'] !== null) {
            $low_dt  = $row['low_data_type'];
            $high_dt = $row['high_data_type'];
            $data_type = ($low_dt !== null && $low_dt !== '')
                ? $low_dt
                : (($high_dt !== null && $high_dt !== '') ? $high_dt : 'flow');
            $range = [
                'low'       => $row['low'],
                'high'      => $row['high'],
                'data_type' => $data_type,
            ];
            break;
        }
    }
    return ['reach' => $reach, 'reach_class' => ['names' => $classes, 'range' => $range]];
}

/**
 * One stamped reviewer-note entry ("[<UTC stamp> maintainer] <note>").
 */
function reviewer_note_entry(string $new): string {
    $stamp = gmdate('Y-m-d H:i') . 'Z';
    return "[$stamp maintainer] " . trim($new);
}

/**
 * Append a new maintainer note to the prior reviewer_note thread, stamped
 * with the current UTC timestamp. Used by the terminal actions (approve /
 * reject / resolve / reply-and-close), where the atomic status flip means
 * only one writer can win — a PHP-side merge from the request-start row is
 * safe there. review_send_reply() does NOT use this: replies don't flip
 * status, so two concurrent reply tabs could both pass the predicate and
 * the second PHP-side merge would drop the first note (PR #119 review);
 * it appends SQL-side inside the UPDATE instead.
 */
function merge_reviewer_note(string $prev, string $new): string {
    $new = trim($new);
    if ($new === '') return $prev;
    return $prev === '' ? reviewer_note_entry($new) : rtrim($prev) . "\n\n" . reviewer_note_entry($new);
}

/**
 * @param array{id: int, target_type: string, target_id: int|null, editor_id: int, submitted_at: string, subject: string|null, payload_json: string, notes_to_maint: string|null, status: string, reviewed_at: string|null, reviewed_by: int|null, reviewer_note: string|null, applied_json: string|null, source_url: string|null} $cr  change_request row.
 * @param array<string, mixed> $applied   payload-shaped overlay (reach + reach_class).
 * @return array{ok: bool, err?: string}
 */
function review_approve(PDO $db, array $cr, array $applied, int $maint_id, string $new_note): array {
    $type = $cr['target_type'];
    $tid  = (int)$cr['target_id'];
    $cur = review_load_target_state($db, $type, $tid);
    if ($cur === null) return ['ok' => false, 'err' => 'Target missing'];

    // SA-lite (dataset-separation D1): approval ENDORSES the change for data
    // review — it freezes the maintainer-edited diff in applied_json and
    // writes NOTHING else. The dataset repo is the only metadata authority
    // (sync-metadata would silently revert a direct DB write at the next
    // deploy), so the maintainer lands the frozen diff as an ordinary
    // kayak_data PR and marks the request resolved once it deploys.
    //
    // Filter the frozen payload against the proposable-fields allowlist:
    // propose_handler only ever writes these keys, but a tampered
    // payload_json must not freeze arbitrary keys for a later operator to
    // copy into the dataset (same defensive posture as the pre-SA-lite
    // SQL-identifier check, review-4 R1.4).
    $reach_payload = $applied['reach'] ?? [];
    if (!is_array($reach_payload)) {
        $reach_payload = [];
    }
    $allowed_fields = array_merge(REACH_TEXT_FIELDS, REACH_FULL_FIELDS);
    foreach (array_keys($reach_payload) as $k) {
        if (!in_array($k, $allowed_fields, true)) {
            error_log('review_approve: dropped non-allowlisted reach field '
                . var_export($k, true) . ' (change_request ' . (string)$cr['id'] . ')');
        }
    }
    $applied['reach'] = array_intersect_key($reach_payload, array_flip($allowed_fields));

    // Claim the row + queue the bridge in ONE transaction: the CAS UPDATE only
    // succeeds if status is still 'pending' (two concurrent maintainers can't
    // both endorse), and the change_request_bridge insert rides the same commit
    // (Tier 2) so an endorsed diff is never left un-queued and a rolled-back
    // freeze leaves no orphan queue row. See docs/PLAN_editor_pr_bridge.md.
    $merged_note = merge_reviewer_note($cr['reviewer_note'] ?? '', $new_note);
    // JSON_PRESERVE_ZERO_FRACTION: the numeric reach fields are floats (M3), so a
    // whole-number coordinate keeps its ".0" — the worker writes str(float), so the
    // frozen value round-trips to the dataset's canonical numeric form.
    $applied_json = json_encode($applied, JSON_UNESCAPED_SLASHES | JSON_PRESERVE_ZERO_FRACTION);
    $applied_json = $applied_json !== false ? $applied_json : '{}';
    $db->beginTransaction();
    try {
        $claim = $db->prepare(
            "UPDATE change_request
             SET status = 'approved', reviewed_at = datetime('now'),
                 reviewed_by = ?, reviewer_note = ?, applied_json = ?
             WHERE id = ? AND status = 'pending'"
        );
        $claim->execute([$maint_id, $merged_note, $applied_json, $cr['id']]);
        if ($claim->rowCount() === 0) {
            $db->rollBack();
            return ['ok' => false, 'err' => 'Already reviewed by another maintainer.'];
        }
        // reach-only path (review.php only offers approve for reach); $tid is the
        // existing reach id ($cur loaded above). bridge_enqueue no-ops unless the
        // frozen diff is bridgeable (reach text/coord, no reach_class).
        bridge_enqueue($db, $cr['id'], $type, $tid, $applied, $applied_json, $maint_id);
        $db->commit();
    } catch (Throwable $e) {
        // Log the full message server-side; never echo it to the response
        // (raw PDOException text leaks schema details and paths).
        if ($db->inTransaction()) {
            $db->rollBack();
        }
        error_log('review_approve: ' . $e->getMessage());
        return ['ok' => false, 'err' => 'endorse failed (see server log for details)'];
    }
    return ['ok' => true];
}

/**
 * Returns true on transition, false if another maintainer already reviewed.
 *
 * @param array{id: int, target_type: string, target_id: int|null, editor_id: int, submitted_at: string, subject: string|null, payload_json: string, notes_to_maint: string|null, status: string, reviewed_at: string|null, reviewed_by: int|null, reviewer_note: string|null, applied_json: string|null, source_url: string|null} $cr change_request row.
 */
function review_reject(PDO $db, array $cr, string $new_note, int $maint_id): bool {
    $merged = merge_reviewer_note($cr['reviewer_note'] ?? '', $new_note);
    $stmt = $db->prepare(
        "UPDATE change_request
         SET status = 'rejected', reviewed_at = datetime('now'),
             reviewed_by = ?, reviewer_note = ?
         WHERE id = ? AND status = 'pending'"
    );
    $stmt->execute([$maint_id, $merged, $cr['id']]);
    return $stmt->rowCount() > 0;
}

/** @param array{id: int, target_type: string, target_id: int|null, editor_id: int, submitted_at: string, subject: string|null, payload_json: string, notes_to_maint: string|null, status: string, reviewed_at: string|null, reviewed_by: int|null, reviewer_note: string|null, applied_json: string|null, source_url: string|null} $cr change_request row. */
function review_notify_editor(PDO $db, array $cr, string $decision, string $note): void {
    $st = $db->prepare('SELECT email FROM editor WHERE id = ?');
    $st->execute([$cr['editor_id']]);
    /** @var array{email: string}|false $row */
    $row = $st->fetch();
    if ($row === false || $row['email'] === '') return;

    $subject = $cr['subject'] ?? '';
    $target_label = $subject !== '' ? $subject : ($cr['target_type'] . ' #' . $cr['target_id']);
    send_email(
        $row['email'],
        "[levels] your proposal was $decision",
        render_editor_decision_email($target_label, $decision, $note)
    );
}

/**
 * Send a maintainer reply without changing the request's status.
 * Returns true on success, false if another maintainer already moved the
 * request out of `pending` (race between two review tabs) — in that case
 * nothing is written and no "still pending" email is sent.
 *
 * @param array{id: int, target_type: string, target_id: int|null, editor_id: int, submitted_at: string, subject: string|null, payload_json: string, notes_to_maint: string|null, status: string, reviewed_at: string|null, reviewed_by: int|null, reviewer_note: string|null, applied_json: string|null, source_url: string|null} $cr change_request row.
 */
function review_send_reply(PDO $db, array $cr, string $reply, int $maint_id): bool {
    $entry = reviewer_note_entry($reply);
    // Same atomic predicate as the terminal actions (approve/reject/
    // resolve/reply-and-close): re-check `pending` inside the UPDATE so a
    // stale tab can't append a note to an already-reviewed row. The append
    // itself is SQL-side (not merged from the request-start $cr row):
    // replies don't flip status, so two concurrent reply tabs can BOTH
    // pass the predicate — a PHP-side merge would last-writer-win and drop
    // the first reply's note (PR #119 review finding). rtrim's char set
    // matches PHP rtrim() defaults (space/tab/LF/CR).
    $stmt = $db->prepare(
        "UPDATE change_request
         SET reviewer_note = CASE
             WHEN reviewer_note IS NULL OR reviewer_note = '' THEN ?
             ELSE rtrim(reviewer_note, char(32) || char(9) || char(10) || char(13))
                  || char(10) || char(10) || ?
           END
         WHERE id = ? AND status = 'pending'"
    );
    $stmt->execute([$entry, $entry, $cr['id']]);
    if ($stmt->rowCount() === 0) return false;

    $st = $db->prepare('SELECT email FROM editor WHERE id = ?');
    $st->execute([$cr['editor_id']]);
    /** @var array{email: string}|false $row */
    $row = $st->fetch();
    if ($row !== false && $row['email'] !== '') {
        $subject = $cr['subject'] ?? '';
        $target_label = $subject !== '' ? $subject : ($cr['target_type'] . ' #' . $cr['target_id']);
        send_email(
            $row['email'],
            "[levels] maintainer reply on your proposal",
            render_editor_reply_email($target_label, $reply)
        );
    }
    return true;
}

/**
 * Terminal close without a payload apply (site comments, mooted proposals).
 * Returns true on transition, false if another maintainer already reviewed.
 *
 * @param array{id: int, target_type: string, target_id: int|null, editor_id: int, submitted_at: string, subject: string|null, payload_json: string, notes_to_maint: string|null, status: string, reviewed_at: string|null, reviewed_by: int|null, reviewer_note: string|null, applied_json: string|null, source_url: string|null} $cr change_request row.
 */
function review_resolve(PDO $db, array $cr, string $new_note, int $maint_id): bool {
    $merged = merge_reviewer_note($cr['reviewer_note'] ?? '', $new_note);
    $stmt = $db->prepare(
        "UPDATE change_request
         SET status = 'resolved', reviewed_at = datetime('now'),
             reviewed_by = ?, reviewer_note = ?
         WHERE id = ? AND status IN ('pending', 'approved')"
    );
    $stmt->execute([$maint_id, $merged, $cr['id']]);
    return $stmt->rowCount() > 0;
}

/**
 * Returns true on transition, false if another maintainer already reviewed.
 *
 * @param array{id: int, target_type: string, target_id: int|null, editor_id: int, submitted_at: string, subject: string|null, payload_json: string, notes_to_maint: string|null, status: string, reviewed_at: string|null, reviewed_by: int|null, reviewer_note: string|null, applied_json: string|null, source_url: string|null} $cr change_request row.
 */
function review_reply_and_close(PDO $db, array $cr, string $reply, int $maint_id): bool {
    $merged = merge_reviewer_note($cr['reviewer_note'] ?? '', $reply);
    $stmt = $db->prepare(
        "UPDATE change_request
         SET status = 'resolved', reviewed_at = datetime('now'),
             reviewed_by = ?, reviewer_note = ?
         WHERE id = ? AND status = 'pending'"
    );
    $stmt->execute([$maint_id, $merged, $cr['id']]);
    if ($stmt->rowCount() === 0) return false;

    $st = $db->prepare('SELECT email FROM editor WHERE id = ?');
    $st->execute([$cr['editor_id']]);
    /** @var array{email: string}|false $row */
    $row = $st->fetch();
    if ($row !== false && $row['email'] !== '') {
        $subject = $cr['subject'] ?? '';
        $target_label = $subject !== '' ? $subject : ($cr['target_type'] . ' #' . $cr['target_id']);
        send_email(
            $row['email'],
            "[levels] your proposal was resolved",
            render_editor_reply_and_close_email($target_label, $reply)
        );
    }
    return true;
}
