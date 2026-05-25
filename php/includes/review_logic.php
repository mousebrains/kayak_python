<?php
declare(strict_types=1);
/**
 * Pure logic for the change_request review flow — extracted from review.php
 * so unit tests can exercise approve/reject/resolve/reply without pulling in
 * the page-rendering controller.
 */

require_once __DIR__ . '/mail.php';

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
    $reach = $st->fetch();
    if (!$reach) return null;

    $st = $db->prepare(
        'SELECT name, low, low_data_type, high, high_data_type
         FROM reach_class WHERE reach_id = ? ORDER BY id'
    );
    $st->execute([$id]);
    $rows = $st->fetchAll();
    $classes = array_column($rows, 'name');
    $range = ['low' => null, 'high' => null, 'data_type' => 'flow'];
    foreach ($rows as $row) {
        if ($row['low'] !== null || $row['high'] !== null) {
            $range = [
                'low'       => $row['low'],
                'high'      => $row['high'],
                'data_type' => $row['low_data_type'] ?: ($row['high_data_type'] ?: 'flow'),
            ];
            break;
        }
    }
    return ['reach' => $reach, 'reach_class' => ['names' => $classes, 'range' => $range]];
}

/**
 * Append a new maintainer note to the prior reviewer_note thread, stamped
 * with the current UTC timestamp. Used by approve / reject / reply so the
 * conversation is preserved across actions.
 */
function merge_reviewer_note(string $prev, string $new): string {
    $new = trim($new);
    if ($new === '') return $prev;
    $stamp = gmdate('Y-m-d H:i') . 'Z';
    $entry = "[$stamp maintainer] " . $new;
    return $prev === '' ? $entry : rtrim($prev) . "\n\n" . $entry;
}

/**
 * @param array<string, mixed> $cr        change_request row.
 * @param array<string, mixed> $applied   payload-shaped overlay (reach + reach_class).
 * @return array{ok: bool, err?: string}
 */
function review_approve(PDO $db, array $cr, array $applied, int $maint_id, string $new_note): array {
    $type = $cr['target_type'];
    $tid  = (int)$cr['target_id'];
    $cur = review_load_target_state($db, $type, $tid);
    if ($cur === null) return ['ok' => false, 'err' => 'Target missing'];

    $db->beginTransaction();
    try {
        // Claim the row up front: only succeeds if status is still 'pending'.
        // Two concurrent maintainers (e.g. two browser tabs) would otherwise
        // both pass the pre-transaction status check, both UPDATE the reach
        // (clobbering each other), and both write edit_history rows.
        $merged_note = merge_reviewer_note((string)($cr['reviewer_note'] ?? ''), $new_note);
        $claim = $db->prepare(
            "UPDATE change_request
             SET status = 'approved', reviewed_at = datetime('now'),
                 reviewed_by = ?, reviewer_note = ?, applied_json = ?
             WHERE id = ? AND status = 'pending'"
        );
        $claim->execute([
            $maint_id,
            $merged_note,
            json_encode($applied, JSON_UNESCAPED_SLASHES),
            $cr['id'],
        ]);
        if ($claim->rowCount() === 0) {
            $db->rollBack();
            return ['ok' => false, 'err' => 'Already reviewed by another maintainer.'];
        }

        // Apply reach columns
        if (($applied['reach'] ?? []) !== []) {
            $sets = [];
            $params = [];
            foreach ($applied['reach'] as $f => $v) {
                $sets[] = "$f = ?";
                $params[] = ($v === '' || $v === null) ? null : $v;
            }
            $sets[] = "updated_at = datetime('now')";
            $params[] = $tid;
            $db->prepare('UPDATE reach SET ' . implode(', ', $sets) . ' WHERE id = ?')
                ->execute($params);

            foreach ($applied['reach'] as $f => $v) {
                $old = $cur['reach'][$f] ?? null;
                $db->prepare(
                    "INSERT INTO edit_history
                     (target_type, target_id, change_request_id, field, old_value, new_value,
                      changed_at, changed_by)
                     VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?)"
                )->execute([$type, $tid, $cr['id'], $f,
                            $old === null ? null : (string)$old,
                            $v   === null ? null : (string)$v,
                            'maintainer:' . $maint_id]);
            }
        }

        // Apply reach_class: names + shared flow range. Replace set atomically.
        if (isset($applied['reach_class'])) {
            $old = $cur['reach_class'];
            $new = $applied['reach_class'];
            $old_dump = json_encode($old, JSON_UNESCAPED_SLASHES);
            $new_dump = json_encode($new, JSON_UNESCAPED_SLASHES);
            if ($old_dump !== $new_dump) {
                $names = $new['names'] ?? [];
                $range = $new['range'] ?? ['low' => null, 'high' => null, 'data_type' => 'flow'];
                $dt = $range['data_type'] ?? 'flow';
                $db->prepare('DELETE FROM reach_class WHERE reach_id = ?')->execute([$tid]);
                $ins = $db->prepare(
                    'INSERT INTO reach_class
                     (reach_id, name, low, low_data_type, high, high_data_type)
                     VALUES (?, ?, ?, ?, ?, ?)'
                );
                foreach ($names as $n) {
                    $ins->execute([$tid, $n,
                                   $range['low']  ?? null, $dt,
                                   $range['high'] ?? null, $dt]);
                }
                $db->prepare(
                    "INSERT INTO edit_history
                     (target_type, target_id, change_request_id, field, old_value, new_value, changed_at, changed_by)
                     VALUES (?, ?, ?, 'reach_class', ?, ?, datetime('now'), ?)"
                )->execute([$type, $tid, $cr['id'], $old_dump, $new_dump, 'maintainer:' . $maint_id]);
            }
        }

        $db->commit();
    } catch (Throwable $e) {
        $db->rollBack();
        // Log the full message server-side; never echo it to the response.
        // The raw PDOException text leaks schema details (column names, FK
        // constraints) and stack-frame paths. Maintainers see the detail
        // in the journal; the user sees a generic confirmation that the
        // attempt failed.
        error_log('review_approve: ' . $e->getMessage());
        return ['ok' => false, 'err' => 'apply failed (see server log for details)'];
    }
    return ['ok' => true];
}

/**
 * Returns true on transition, false if another maintainer already reviewed.
 *
 * @param array<string, mixed> $cr change_request row.
 */
function review_reject(PDO $db, array $cr, string $new_note, int $maint_id): bool {
    $merged = merge_reviewer_note((string)($cr['reviewer_note'] ?? ''), $new_note);
    $stmt = $db->prepare(
        "UPDATE change_request
         SET status = 'rejected', reviewed_at = datetime('now'),
             reviewed_by = ?, reviewer_note = ?
         WHERE id = ? AND status = 'pending'"
    );
    $stmt->execute([$maint_id, $merged, $cr['id']]);
    return $stmt->rowCount() > 0;
}

/** @param array<string, mixed> $cr change_request row. */
function review_notify_editor(PDO $db, array $cr, string $decision, string $note): void {
    $st = $db->prepare('SELECT email FROM editor WHERE id = ?');
    $st->execute([$cr['editor_id']]);
    $row = $st->fetch();
    if (!$row || ($row['email'] ?? '') === '') return;

    $target_label = $cr['subject'] ?: ($cr['target_type'] . ' #' . $cr['target_id']);
    send_email(
        (string)$row['email'],
        "[levels] your proposal was $decision",
        render_editor_decision_email($target_label, $decision, $note)
    );
}

/**
 * Send a maintainer reply without changing the request's status.
 *
 * @param array<string, mixed> $cr change_request row.
 */
function review_send_reply(PDO $db, array $cr, string $reply, int $maint_id): void {
    $merged = merge_reviewer_note((string)($cr['reviewer_note'] ?? ''), $reply);
    $db->prepare('UPDATE change_request SET reviewer_note = ? WHERE id = ?')
        ->execute([$merged, $cr['id']]);

    $st = $db->prepare('SELECT email FROM editor WHERE id = ?');
    $st->execute([$cr['editor_id']]);
    $row = $st->fetch();
    if ($row && ($row['email'] ?? '') !== '') {
        $target_label = $cr['subject'] ?: ($cr['target_type'] . ' #' . $cr['target_id']);
        send_email(
            (string)$row['email'],
            "[levels] maintainer reply on your proposal",
            render_editor_reply_email($target_label, $reply)
        );
    }
}

/**
 * Terminal close without a payload apply (site comments, mooted proposals).
 * Returns true on transition, false if another maintainer already reviewed.
 *
 * @param array<string, mixed> $cr change_request row.
 */
function review_resolve(PDO $db, array $cr, string $new_note, int $maint_id): bool {
    $merged = merge_reviewer_note((string)($cr['reviewer_note'] ?? ''), $new_note);
    $stmt = $db->prepare(
        "UPDATE change_request
         SET status = 'resolved', reviewed_at = datetime('now'),
             reviewed_by = ?, reviewer_note = ?
         WHERE id = ? AND status = 'pending'"
    );
    $stmt->execute([$maint_id, $merged, $cr['id']]);
    return $stmt->rowCount() > 0;
}

/**
 * Returns true on transition, false if another maintainer already reviewed.
 *
 * @param array<string, mixed> $cr change_request row.
 */
function review_reply_and_close(PDO $db, array $cr, string $reply, int $maint_id): bool {
    $merged = merge_reviewer_note((string)($cr['reviewer_note'] ?? ''), $reply);
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
    $row = $st->fetch();
    if ($row && ($row['email'] ?? '') !== '') {
        $target_label = $cr['subject'] ?: ($cr['target_type'] . ' #' . $cr['target_id']);
        send_email(
            (string)$row['email'],
            "[levels] your proposal was resolved",
            render_editor_reply_and_close_email($target_label, $reply)
        );
    }
    return true;
}
