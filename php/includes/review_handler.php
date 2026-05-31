<?php

declare(strict_types=1);

/**
 * Handler for /review.php — maintainer moderation page for the
 * change_request queue.
 *
 * Called from review.php after require_editor_feature() +
 * require_maintainer() have run. Dispatches POST actions, then
 * renders either the detail view (?id=N) or the list view.
 *
 * Helpers prefixed with `_review_` are file-private and carry the
 * file's name as part of the prefix (Tier 5 CI-lesson note in
 * docs/done/PLAN_php_layer_split.md). This matters here because review.php
 * already requires review_logic.php which exports several
 * `review_*` helpers (review_approve, review_reject, …); the
 * file-private helpers stay underscored + file-prefixed to keep the
 * boundary unambiguous.
 *
 * The `array{...}` row shapes on the helpers below are verified against
 * the dev SQLite schema (PDO, EMULATE_PREPARES=false): INTEGER→int,
 * TEXT/VARCHAR/DATETIME→string, with `|null` for every `notnull=0`
 * column. The detail/list helpers additionally carry the three editor
 * columns aliased by the `JOIN editor` (editor_email/name/status).
 */

require_once __DIR__ . '/db.php';
require_once __DIR__ . '/pubhash_request.php';
require_once __DIR__ . '/auth.php';
require_once __DIR__ . '/sanity.php';
require_once __DIR__ . '/review_logic.php';
require_once __DIR__ . '/header.php';
require_once __DIR__ . '/footer.php';

/** Status values valid for the ?status= list-view filter. */
const REVIEW_LIST_STATUSES = ['pending', 'approved', 'rejected', 'resolved', 'all'];

/**
 * Dispatch and write the full HTTP response.
 *
 * @param array<string, mixed> $maint  The current_editor() row for the maintainer.
 */
function handle_review_request(PDO $db, array $maint): void
{
    $cr_id_get = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT);
    $cr_id = is_int($cr_id_get) && $cr_id_get !== 0
          ? $cr_id_get
          : filter_input(INPUT_POST, 'id', FILTER_VALIDATE_INT);
    $cr_id = is_int($cr_id) ? $cr_id : null;
    $action = isset($_POST['action']) ? (string)$_POST['action'] : null;

    $flash = null;
    $flash_err = null;

    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        [$flash, $flash_err] = _review_handle_post($db, $cr_id, $action, (int)$maint['id']);
    }

    $csrf = htmlspecialchars(csrf_token());
    header('Cache-Control: no-store');

    if ($cr_id !== null && $cr_id !== 0) {
        _render_review_detail($db, $cr_id, $flash, $flash_err, $csrf);
        return;
    }

    _render_review_list($db, $flash, $flash_err);
}

/**
 * POST dispatch — verifies CSRF, loads the CR, runs the requested
 * action against review_logic, returns a [flash, flash_err] pair.
 * Exits the script for hard failures (missing id, 404 on the CR).
 *
 * @return array{0: ?string, 1: ?string}  [flash, flash_err]
 */
function _review_handle_post(PDO $db, ?int $cr_id, ?string $action, int $maint_id): array
{
    require_csrf();
    if ($cr_id === null || $cr_id === 0) {
        http_terminate(400, 'Missing id');
    }
    $st = $db->prepare('SELECT * FROM change_request WHERE id = ?');
    $st->execute([$cr_id]);
    /** @var array{id: int, target_type: string, target_id: int|null, editor_id: int, submitted_at: string, subject: string|null, payload_json: string, notes_to_maint: string|null, status: string, reviewed_at: string|null, reviewed_by: int|null, reviewer_note: string|null, applied_json: string|null, source_url: string|null}|false $cr */
    $cr = $st->fetch();
    if ($cr === false) {
        http_terminate(404, 'change_request not found');
    }
    if ($cr['status'] !== 'pending') {
        return [null, 'This request has already been ' . $cr['status'] . '.'];
    }

    switch ($action) {
        case 'approve':
            $applied = _review_build_approve_payload($cr);
            $approve_note = trim((string)($_POST['reviewer_note'] ?? ''));
            $result = review_approve($db, $cr, $applied, $maint_id, $approve_note);
            if ($result['ok']) {
                review_notify_editor($db, $cr, 'approved', $approve_note);
                return ['Approved and applied.', null];
            }
            return [null, $result['err'] ?? 'Apply failed.'];

        case 'reject':
            $note = trim((string)($_POST['reviewer_note'] ?? ''));
            if (review_reject($db, $cr, $note, $maint_id)) {
                review_notify_editor($db, $cr, 'rejected', $note);
                return ['Rejected.', null];
            }
            return [null, 'Already reviewed by another maintainer.'];

        case 'reply':
            $note = trim((string)($_POST['reviewer_note'] ?? ''));
            if ($note === '') {
                return [null, 'Reply cannot be empty.'];
            }
            review_send_reply($db, $cr, $note, $maint_id);
            return ['Reply sent — proposal kept pending.', null];

        case 'reply_and_close':
            $note = trim((string)($_POST['reviewer_note'] ?? ''));
            if ($note === '') {
                return [null, 'Reply cannot be empty.'];
            }
            return review_reply_and_close($db, $cr, $note, $maint_id)
                ? ['Reply sent and proposal marked resolved.', null]
                : [null, 'Already reviewed by another maintainer.'];

        case 'resolve':
            $note = trim((string)($_POST['reviewer_note'] ?? ''));
            if (review_resolve($db, $cr, $note, $maint_id)) {
                review_notify_editor($db, $cr, 'resolved', $note);
                return ['Marked resolved.', null];
            }
            return [null, 'Already reviewed by another maintainer.'];
    }

    // POST with no/unknown action — render the page with no flash.
    return [null, null];
}

/**
 * Reconstruct the (possibly maintainer-tweaked) payload from POST
 * fields. Each `reach_<field>` overlay wins over the editor's
 * proposal; `classes_present`=1 unlocks the reach_class block.
 *
 * @param  array{payload_json: string} $cr  change_request row (only payload_json is read here).
 * @return array<string, mixed>
 */
function _review_build_approve_payload(array $cr): array
{
    $decoded = json_decode($cr['payload_json'], true);
    $payload = is_array($decoded) ? $decoded : [];
    $applied = ['reach' => [], 'reach_class' => null];

    /** @var array<string, mixed> $reach */
    $reach = is_array($payload['reach'] ?? null) ? $payload['reach'] : [];
    if ($reach !== []) {
        foreach (array_keys($reach) as $f) {
            $key = "reach_$f";
            $applied['reach'][$f] = array_key_exists($key, $_POST)
                ? trim((string)$_POST[$key])
                : $reach[$f];
        }
    }
    if (isset($payload['reach_class']) && isset($_POST['classes_present'])) {
        $raw = trim((string)($_POST['classes'] ?? ''));
        $names = $raw === '' ? [] : array_values(array_filter(array_map('trim', explode(',', $raw)), fn($s) => $s !== ''));
        $lo = trim((string)($_POST['flow_low']       ?? ''));
        $hi = trim((string)($_POST['flow_high']      ?? ''));
        $dt = trim((string)($_POST['flow_data_type'] ?? 'flow'));
        $applied['reach_class'] = [
            'names' => $names,
            'range' => [
                'low'       => $lo !== '' ? (float)$lo : null,
                'high'      => $hi !== '' ? (float)$hi : null,
                'data_type' => $dt !== '' ? $dt : 'flow',
            ],
        ];
    } else {
        unset($applied['reach_class']);
    }
    return $applied;
}

/**
 * Detail view — single change_request row with diff + editable
 * approve form (when pending) or maintainer-notes + applied-payload
 * (when terminal).
 */
function _render_review_detail(
    PDO $db,
    int $cr_id,
    ?string $flash,
    ?string $flash_err,
    string $csrf,
): void {
    $st = $db->prepare(
        'SELECT cr.*, e.email AS editor_email, e.display_name AS editor_name, e.status AS editor_status
         FROM change_request cr JOIN editor e ON e.id = cr.editor_id
         WHERE cr.id = ?'
    );
    $st->execute([$cr_id]);
    /** @var array{id: int, target_type: string, target_id: int|null, editor_id: int, submitted_at: string, subject: string|null, payload_json: string, notes_to_maint: string|null, status: string, reviewed_at: string|null, reviewed_by: int|null, reviewer_note: string|null, applied_json: string|null, source_url: string|null, editor_email: string, editor_name: string|null, editor_status: string}|false $cr */
    $cr = $st->fetch();
    if ($cr === false) {
        require_once __DIR__ . '/error.php';
        render_error_page(
            404,
            'Not found',
            '<p>No change request with id ' . $cr_id . ' exists.</p>'
            . '<p><a href="/review.php">Back to the review queue</a></p>'
        );
        return;
    }

    $decoded = json_decode($cr['payload_json'], true);
    $payload = is_array($decoded) ? $decoded : [];
    $applied_decoded = json_decode($cr['applied_json'] ?? 'null', true);
    $applied = is_array($applied_decoded) ? $applied_decoded : null;
    $cur = $cr['target_type'] === 'reach' && $cr['target_id'] !== null
        ? review_load_target_state($db, 'reach', $cr['target_id'])
        : null;

    $subject = $cr['subject'] ?? '';
    include_header('Review: ' . ($subject !== '' ? $subject : 'change_request #' . $cr['id']));
    echo '<h2>Review: ' . htmlspecialchars($subject) . '</h2>';
    _render_review_flash($flash, $flash_err);
    _render_review_meta_table($cr, $payload);

    if ($cr['status'] !== 'pending') {
        _render_review_terminal_state($cr, $applied);
        include_footer();
        return;
    }

    _render_review_form($cr, $payload, $cur, $csrf);
    include_footer();
}

/** Inline flash banners (green for success, red for error). */
function _render_review_flash(?string $flash, ?string $flash_err): void
{
    if ($flash !== null && $flash !== '') {
        echo '<p style="padding:.5rem;background:#e8f4ea;border:1px solid #b7dcc0;border-radius:4px">'
           . htmlspecialchars($flash) . '</p>';
    }
    if ($flash_err !== null && $flash_err !== '') {
        echo '<p style="padding:.5rem;background:#fde8e8;border:1px solid #f5b5b5;border-radius:4px">'
           . htmlspecialchars($flash_err) . '</p>';
    }
}

/**
 * From / Submitted / Page / Status / Reach / Message / Notes table.
 *
 * @param array{id: int, target_type: string, target_id: int|null, editor_id: int, submitted_at: string, subject: string|null, payload_json: string, notes_to_maint: string|null, status: string, reviewed_at: string|null, reviewed_by: int|null, reviewer_note: string|null, applied_json: string|null, source_url: string|null, editor_email: string, editor_name: string|null, editor_status: string} $cr
 * @param array<string, mixed> $payload
 */
function _render_review_meta_table(array $cr, array $payload): void
{
    echo '<table class="desc-table">';
    echo '<tr><td>From</td><td>' . htmlspecialchars($cr['editor_email'])
       . ' (' . htmlspecialchars($cr['editor_status']) . ')</td></tr>';
    echo '<tr><td>Submitted</td><td>' . htmlspecialchars($cr['submitted_at']) . '</td></tr>';
    if (($cr['source_url'] ?? '') !== '') {
        $src = (string)$cr['source_url'];
        echo '<tr><td>Page</td><td><a href="' . htmlspecialchars($src) . '">'
           . htmlspecialchars($src) . '</a></td></tr>';
    }
    echo '<tr><td>Status</td><td>' . htmlspecialchars($cr['status']) . '</td></tr>';
    if ($cr['target_type'] === 'reach' && is_int($cr['target_id'])) {
        echo '<tr><td>Reach</td><td><a href="' . pubhash_url('description', $cr['target_id'])
           . '">description</a></td></tr>';
    }
    if (($payload['body'] ?? '') !== '') {
        echo '<tr><td>Message</td><td><pre style="white-space:pre-wrap;margin:0">'
           . htmlspecialchars((string)$payload['body']) . '</pre></td></tr>';
    }
    if (($cr['notes_to_maint'] ?? '') !== '') {
        echo '<tr><td>Notes</td><td><pre style="white-space:pre-wrap;margin:0">'
           . htmlspecialchars((string)$cr['notes_to_maint']) . '</pre></td></tr>';
    }
    echo '</table>';
}

/**
 * Terminal-state view (approved/rejected/resolved): maintainer notes
 * + applied-payload JSON + back link. No form, no buttons.
 *
 * @param array{id: int, target_type: string, target_id: int|null, editor_id: int, submitted_at: string, subject: string|null, payload_json: string, notes_to_maint: string|null, status: string, reviewed_at: string|null, reviewed_by: int|null, reviewer_note: string|null, applied_json: string|null, source_url: string|null, editor_email: string, editor_name: string|null, editor_status: string} $cr
 * @param array<array-key, mixed>|null $applied  decoded applied_json payload (untrusted shape).
 */
function _render_review_terminal_state(array $cr, ?array $applied): void
{
    if (($cr['reviewer_note'] ?? '') !== '') {
        echo '<h3>Maintainer notes</h3>';
        echo '<pre style="white-space:pre-wrap;background:#f6f8fa;border:1px solid #e1e4e8;border-radius:4px;padding:.5rem">'
           . htmlspecialchars((string)$cr['reviewer_note']) . '</pre>';
    }
    if (($applied ?? []) !== []) {
        $applied_json = json_encode($applied, JSON_PRETTY_PRINT);
        echo '<h3>Applied payload</h3><pre style="white-space:pre-wrap">'
           . htmlspecialchars($applied_json !== false ? $applied_json : '') . '</pre>';
    }
    echo '<p><a href="/review.php">Back to queue</a></p>';
}

/**
 * Editable approve form — reach-field overlay table, optional
 * reach_class block, reviewer-note textarea, action buttons.
 *
 * @param array{id: int, target_type: string, target_id: int|null, editor_id: int, submitted_at: string, subject: string|null, payload_json: string, notes_to_maint: string|null, status: string, reviewed_at: string|null, reviewed_by: int|null, reviewer_note: string|null, applied_json: string|null, source_url: string|null, editor_email: string, editor_name: string|null, editor_status: string} $cr
 * @param array<string, mixed> $payload
 * @param array{reach: array<string, mixed>, reach_class: array{names: list<string>, range: array{low: ?float, high: ?float, data_type: string}}}|null $cur
 */
function _render_review_form(array $cr, array $payload, ?array $cur, string $csrf): void
{
    echo '<form method="POST" action="/review.php">';
    echo '<input type="hidden" name="csrf_token" value="' . $csrf . '">';
    echo '<input type="hidden" name="id" value="' . $cr['id'] . '">';

    $reach = is_array($payload['reach'] ?? null) ? $payload['reach'] : [];
    if ($reach !== []) {
        _render_review_reach_fields($reach, $cur);
    }
    if (isset($payload['reach_class']) && is_array($payload['reach_class'])) {
        _render_review_class_block($payload['reach_class'], $cur);
    }

    echo '<h3 style="margin-top:1rem">Decision</h3>';
    echo '<label>Reviewer note / reply (included in the email to the editor)</label>';
    echo '<textarea name="reviewer_note" style="width:100%;min-height:4em"></textarea>';
    if (($cr['reviewer_note'] ?? '') !== '') {
        echo '<p style="margin-top:.25rem;font-size:.8rem;color:var(--c-text-muted)">Earlier notes:</p>';
        echo '<pre style="white-space:pre-wrap;background:#f6f8fa;border:1px solid #e1e4e8;border-radius:4px;padding:.5rem;font-size:.8rem">'
           . htmlspecialchars((string)$cr['reviewer_note']) . '</pre>';
    }
    echo '<p style="margin-top:.5rem">';
    if ($cr['target_type'] === 'reach') {
        echo '<button type="submit" name="action" value="approve">Approve and apply</button>';
    }
    echo ' <button type="submit" name="action" value="reply">Send reply (keep pending)</button>';
    echo ' <button type="submit" name="action" value="reply_and_close">Reply and close</button>';
    echo ' <button type="submit" name="action" value="resolve">Mark resolved</button>';
    echo ' <button type="submit" name="action" value="reject">Reject</button>';
    echo ' <a href="/review.php" style="margin-left:1rem">Back to queue</a>';
    echo '</p>';
    echo '</form>';
}

/**
 * Three-column reach-fields table — Field / Current / Proposed
 * (editable). description+features use <textarea>, everything else
 * uses <input type=text>.
 *
 * @param array<string, mixed> $reach_fields  proposed reach-field overlay (decoded JSON).
 * @param array{reach: array<string, mixed>, reach_class: array{names: list<string>, range: array{low: ?float, high: ?float, data_type: string}}}|null $cur
 */
function _render_review_reach_fields(array $reach_fields, ?array $cur): void
{
    echo '<h3>Reach field changes</h3>';
    echo '<table class="desc-table">';
    echo '<tr><th>Field</th><th>Current</th><th>Proposed (editable)</th></tr>';
    foreach ($reach_fields as $f => $v) {
        $cur_val = $cur !== null ? (string)($cur['reach'][$f] ?? '') : '';
        $is_long = in_array($f, ['description', 'features'], true);
        echo '<tr><td>' . htmlspecialchars($f) . '</td>';
        echo '<td><pre style="white-space:pre-wrap;margin:0;max-width:30em">'
           . htmlspecialchars($cur_val) . '</pre></td>';
        if ($is_long) {
            echo '<td><textarea name="reach_' . htmlspecialchars($f) . '" style="width:100%;min-height:6em">'
               . htmlspecialchars((string)$v) . '</textarea></td>';
        } else {
            echo '<td><input type="text" name="reach_' . htmlspecialchars($f) . '" value="'
               . htmlspecialchars((string)$v) . '" style="width:100%"></td>';
        }
        echo '</tr>';
    }
    echo '</table>';
}

/**
 * reach_class block — classes input + flow range row + data-type
 * select. Renders current values inline above the editable widgets.
 *
 * @param array<array-key, mixed> $proposed  proposed reach_class block (decoded JSON; untrusted shape).
 * @param array{reach: array<string, mixed>, reach_class: array{names: list<string>, range: array{low: ?float, high: ?float, data_type: string}}}|null $cur
 */
function _render_review_class_block(array $proposed, ?array $cur): void
{
    echo '<h3>Classes and flow range (editable)</h3>';
    echo '<input type="hidden" name="classes_present" value="1">';
    $cur_names = $cur['reach_class']['names'] ?? [];
    $cur_range = $cur['reach_class']['range'] ?? ['low' => null, 'high' => null, 'data_type' => 'flow'];
    // names is decoded from an untrusted JSON payload; coerce each entry to
    // string (matching implode's own runtime coercion) so the value typechecks.
    $p_names = is_array($proposed['names'] ?? null)
        ? array_map(static fn (mixed $n): string => (string) $n, $proposed['names'])
        : [];
    /** @var array<string, mixed> $p_range */
    $p_range = is_array($proposed['range'] ?? null)
        ? $proposed['range']
        : ['low' => null, 'high' => null, 'data_type' => 'flow'];
    $cur_names_str = implode(', ', $cur_names);
    echo '<p>Current classes: <code>' . htmlspecialchars($cur_names_str !== '' ? $cur_names_str : '(none)') . '</code></p>';
    $cur_range_str = ($cur_range['low'] ?? '-') . ' to ' . ($cur_range['high'] ?? '-')
                   . ' ' . $cur_range['data_type'];
    echo '<p>Current range: <code>' . htmlspecialchars($cur_range_str) . '</code></p>';
    echo '<label>Proposed classes (comma-separated)</label>';
    echo '<input type="text" name="classes" value="' . htmlspecialchars(implode(', ', $p_names)) . '" style="width:100%">';
    echo '<table style="margin-top:.5rem"><tr><th>Low</th><th>High</th><th>Type</th></tr><tr>';
    echo '<td><input type="number" step="any" name="flow_low" value="'
       . htmlspecialchars((string)($p_range['low']  ?? '')) . '"></td>';
    echo '<td><input type="number" step="any" name="flow_high" value="'
       . htmlspecialchars((string)($p_range['high'] ?? '')) . '"></td>';
    echo '<td><select name="flow_data_type">';
    $sel = $p_range['data_type'] ?? 'flow';
    foreach (['flow', 'gauge'] as $dt) {
        echo '<option value="' . $dt . '"' . ($sel === $dt ? ' selected' : '') . '>' . $dt . '</option>';
    }
    echo '</select></td></tr></table>';
}

/**
 * Queue list view — status filter row + the 5-column CR table.
 */
function _render_review_list(PDO $db, ?string $flash, ?string $flash_err): void
{
    $q_status = isset($_GET['status']) ? (string)$_GET['status'] : 'pending';
    if (!in_array($q_status, REVIEW_LIST_STATUSES, true)) {
        $q_status = 'pending';
    }

    $where = $q_status === 'all' ? '' : 'WHERE cr.status = ?';
    $params = $q_status === 'all' ? [] : [$q_status];
    $sql = "SELECT cr.id, cr.target_type, cr.target_id, cr.subject, cr.status,
                   cr.submitted_at, cr.notes_to_maint,
                   e.email AS editor_email, e.status AS editor_status
            FROM change_request cr JOIN editor e ON e.id = cr.editor_id
            $where
            ORDER BY cr.submitted_at DESC
            LIMIT 200";
    $stmt = $db->prepare($sql);
    $stmt->execute($params);
    $rows = $stmt->fetchAll();

    include_header('Review queue');
    echo '<h2>Review queue</h2>';
    _render_review_flash($flash, $flash_err);

    echo '<p style="font-size:.85rem">Status: ';
    foreach (REVIEW_LIST_STATUSES as $s) {
        $cls = $s === $q_status ? ' style="font-weight:700"' : '';
        echo '<a href="/review.php?status=' . $s . '"' . $cls . '>' . $s . '</a> &nbsp;';
    }
    echo '<a href="/admin.php" style="float:right">Admin</a>';
    echo '</p>';

    if ($rows === []) {
        echo '<p>No proposals.</p>';
    } else {
        echo '<table class="desc-table">';
        echo '<tr><th>Submitted</th><th>From</th><th>Target</th><th>Subject</th><th>Status</th></tr>';
        foreach ($rows as $r) {
            $tgt = (string)$r['target_type'];
            if ($r['target_id']) {
                $tgt .= ' #' . (int)$r['target_id'];
            }
            echo '<tr>'
               . '<td>' . htmlspecialchars((string)$r['submitted_at']) . '</td>'
               . '<td>' . htmlspecialchars((string)$r['editor_email']) . ' <span style="color:var(--c-text-muted);font-size:.75rem">('
                    . htmlspecialchars((string)$r['editor_status']) . ')</span></td>'
               . '<td>' . htmlspecialchars($tgt) . '</td>'
               . '<td><a href="/review.php?id=' . (int)$r['id'] . '">'
                    . htmlspecialchars((string)$r['subject']) . '</a></td>'
               . '<td>' . htmlspecialchars((string)$r['status']) . '</td>'
               . '</tr>';
        }
        echo '</table>';
    }

    include_footer();
}
