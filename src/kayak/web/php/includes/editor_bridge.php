<?php

declare(strict_types=1);

/**
 * Tier 2 of the editor → kayak_data bridge: when a maintainer endorses (or
 * direct-edits) a *bridgeable* change_request, queue a `change_request_bridge`
 * row so the worker (Tier 4) can open exactly one kayak_data PR for the frozen
 * diff. See docs/PLAN_editor_pr_bridge.md.
 *
 * Scope guardrails (SA-lite D1 — the dataset repo is the only metadata
 * authority):
 *   - This file writes ONLY `change_request_bridge`, which is engine *runtime*
 *     state (absent from layout.CONTRACT_CSVS, never synced) — never a metadata
 *     table. PHP still holds no git credential and opens no PR; it only marks a
 *     request as queued for the privileged CLI worker.
 *   - bridge_enqueue() must run inside the caller's open transaction (the same
 *     one that freezes applied_json) so an endorsed diff is never left
 *     un-queued and a rolled-back freeze never leaves an orphan queue row.
 *
 * Bridgeability mirrors the Tier 3 adapter (kayak.editor_bridge.dataset_patch):
 * only `reach` (text/coord fields, NO reach_class) and `gauge` diffs can be
 * turned into a PR today. reach_class, site comments, and reply-only requests
 * stay manual.
 */

/** Targets a bridge PR can patch today (matches dataset_patch.apply_change). */
const BRIDGE_TARGET_TYPES = ['reach', 'gauge'];

/**
 * Can the Tier 3 adapter turn this endorsed payload into a kayak_data PR?
 *
 *   - reach: a non-empty `reach` field diff AND no `reach_class` key. The
 *     adapter rejects any payload carrying `reach_class` (its set-of-names shape
 *     isn't row-id-aware yet — plan §3 / dataset_patch.apply_change), so a reach
 *     CR that also changes classes stays a manual PR. array_key_exists (not
 *     isset/!empty) matches the adapter's strict `"reach_class" in applied_json`.
 *   - gauge: a non-empty `gauge` field diff.
 *
 * Everything else (site/source/comment, reply-only/body-only, class-only) is
 * not bridgeable.
 *
 * @param array<string, mixed> $applied  decoded applied_json (the frozen diff)
 */
function bridge_is_bridgeable(string $target_type, array $applied): bool
{
    if ($target_type === 'reach') {
        if (array_key_exists('reach_class', $applied)) {
            return false;
        }
        $reach = $applied['reach'] ?? null;
        return is_array($reach) && $reach !== [];
    }
    if ($target_type === 'gauge') {
        $gauge = $applied['gauge'] ?? null;
        return is_array($gauge) && $gauge !== [];
    }
    return false;
}

/**
 * Current DB values of the fields being changed, keyed by table — the worker's
 * drift base (`reviewed_base_json`). Shape mirrors applied_json so Tier 4 can
 * lift `[target_type]` straight into the adapter's `expected_base`:
 * `{"reach": {"description": "old", ...}}` / `{"gauge": {"location": "old"}}`.
 *
 * Returns null if the target row is gone (caller then skips queueing — a
 * vanished target is handled manually, never queued blind).
 *
 * @param array<string, mixed> $applied  decoded applied_json (must be bridgeable)
 * @return array<string, array<string, mixed>>|null
 */
function bridge_capture_base(PDO $db, string $target_type, int $target_id, array $applied): ?array
{
    if (!in_array($target_type, BRIDGE_TARGET_TYPES, true)) {
        // Defense in depth: the table name is interpolated below, so refuse
        // anything outside the fixed allowlist (callers gate on
        // bridge_is_bridgeable first, which already guarantees this).
        throw new InvalidArgumentException('non-bridgeable target_type: ' . $target_type);
    }
    $diff = $applied[$target_type] ?? null;
    if (!is_array($diff) || $diff === []) {
        return null;
    }

    $st = $db->prepare("SELECT * FROM {$target_type} WHERE id = ?");
    $st->execute([$target_id]);
    /** @var array<string, mixed>|false $row */
    $row = $st->fetch();
    if ($row === false) {
        return null;
    }

    $base = [];
    foreach (array_keys($diff) as $field) {
        $f = (string)$field;
        $base[$f] = array_key_exists($f, $row) ? $row[$f] : null;
    }
    return [$target_type => $base];
}

/**
 * Queue a `change_request_bridge` row for an endorsed change_request, inside
 * the caller's open transaction.
 *
 * Returns true when a row was inserted, false on a no-op (payload not
 * bridgeable, target vanished, or a row already exists for this request —
 * idempotent re-endorse via `ON CONFLICT(change_request_id) DO NOTHING`).
 * Lets any PDO error propagate so the caller's transaction rolls back (a
 * frozen diff must never be silently left un-queued).
 *
 * `base_dataset_sha` is intentionally left NULL: PHP cannot know the dataset
 * repo's current main SHA (it holds no checkout), so the worker fills it when
 * it runs.
 *
 * @param array<string, mixed> $applied           decoded applied_json (the frozen diff)
 * @param string               $applied_json_str  the exact frozen-diff JSON string written to change_request.applied_json
 */
function bridge_enqueue(
    PDO $db,
    int $cr_id,
    string $target_type,
    ?int $target_id,
    array $applied,
    string $applied_json_str,
    ?int $queued_by,
): bool {
    if ($target_id === null) {
        return false;
    }
    if (!bridge_is_bridgeable($target_type, $applied)) {
        return false;
    }

    $base = bridge_capture_base($db, $target_type, $target_id, $applied);
    if ($base === null) {
        return false;
    }
    // JSON_PRESERVE_ZERO_FRACTION: PDO (EMULATE_PREPARES=false) returns a numeric
    // cell as a PHP float, so a whole-number value like optimal_flow 800.0 would
    // json_encode to `800` and drop the ".0". The worker's drift check renders the
    // base with Python str() (str(800.0) == "800.0") and compares it to the dataset
    // CSV cell ("800.0"), so the dropped ".0" would false-conflict an unchanged
    // value. Preserving the fraction keeps base ⇔ CSV text aligned.
    $base_json = json_encode($base, JSON_UNESCAPED_SLASHES | JSON_PRESERVE_ZERO_FRACTION);
    if ($base_json === false) {
        // Never queue with a NULL base: the worker treats a missing base as
        // "skip drift check", silently downgrading safety. Fail loudly so the
        // caller's transaction rolls the whole endorse back (a frozen diff is
        // either queued WITH its drift base or not frozen at all).
        throw new RuntimeException(
            'bridge: failed to encode reviewed base for change_request ' . $cr_id
        );
    }

    $st = $db->prepare(
        "INSERT INTO change_request_bridge
            (change_request_id, state, attempt, reviewed_base_json,
             applied_json_sha256, queued_by, queued_at)
         VALUES (?, 'queued', 1, ?, ?, ?, datetime('now'))
         ON CONFLICT (change_request_id) DO NOTHING"
    );
    $st->execute([
        $cr_id,
        $base_json,
        hash('sha256', $applied_json_str),
        $queued_by,
    ]);
    return $st->rowCount() > 0;
}
