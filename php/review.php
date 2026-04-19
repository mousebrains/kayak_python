<?php
declare(strict_types=1);
/**
 * Maintainer moderation page for change_request proposals.
 *
 * GET  /review.php                 List pending
 * GET  /review.php?id=N            Detail + diff + editable form
 * POST /review.php  action=approve Apply the (possibly tweaked) payload
 * POST /review.php  action=reject  Mark rejected + optional reviewer note
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/mail.php';
require_once __DIR__ . '/includes/sanity.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

require_editor_feature();
$maint = require_maintainer();
$db = get_db();

// ---------------------------------------------------------------------------
// Apply helpers
// ---------------------------------------------------------------------------

/**
 * Load the current state for a change_request's target so we can diff
 * and later build edit_history rows. Returns associative state arrays
 * keyed by 'reach', 'reach_level', 'reach_class'.
 */
function review_load_target_state(PDO $db, string $type, int $id): ?array {
    if ($type !== 'reach') return null;
    $st = $db->prepare('SELECT * FROM reach WHERE id = ?');
    $st->execute([$id]);
    $reach = $st->fetch();
    if (!$reach) return null;
    $st = $db->prepare(
        'SELECT level, low, low_data_type, high, high_data_type
         FROM reach_level WHERE reach_id = ? ORDER BY
           CASE level WHEN \'low\' THEN 0 WHEN \'okay\' THEN 1 WHEN \'high\' THEN 2 ELSE 3 END'
    );
    $st->execute([$id]);
    $levels = [];
    foreach ($st->fetchAll() as $row) $levels[$row['level']] = $row;

    $st = $db->prepare('SELECT name FROM reach_class WHERE reach_id = ? ORDER BY id');
    $st->execute([$id]);
    $classes = array_column($st->fetchAll(), 'name');

    return ['reach' => $reach, 'reach_level' => $levels, 'reach_class' => $classes];
}

function review_approve(PDO $db, array $cr, array $applied, int $maint_id): array {
    $type = $cr['target_type'];
    $tid  = (int)$cr['target_id'];
    $cur = review_load_target_state($db, $type, $tid);
    if ($cur === null) return ['ok' => false, 'err' => 'Target missing'];

    $db->beginTransaction();
    try {
        // Apply reach columns
        if (!empty($applied['reach'])) {
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
                            'editor:' . $cr['editor_id']]);
            }
        }

        // Apply reach_level: replace the set atomically
        if (isset($applied['reach_level'])) {
            $old_dump = json_encode($cur['reach_level'], JSON_UNESCAPED_SLASHES);
            $new_dump = json_encode($applied['reach_level'], JSON_UNESCAPED_SLASHES);
            if ($old_dump !== $new_dump) {
                $db->prepare('DELETE FROM reach_level WHERE reach_id = ?')->execute([$tid]);
                $ins = $db->prepare(
                    'INSERT INTO reach_level (reach_id, level, low, low_data_type, high, high_data_type)
                     VALUES (?, ?, ?, ?, ?, ?)'
                );
                foreach ($applied['reach_level'] as $row) {
                    $ins->execute([
                        $tid,
                        $row['level'],
                        $row['low']  ?? null,
                        $row['low_data_type']  ?? 'flow',
                        $row['high'] ?? null,
                        $row['low_data_type']  ?? 'flow',  // mirror type for both sides
                    ]);
                }
                $db->prepare(
                    "INSERT INTO edit_history
                     (target_type, target_id, change_request_id, field, old_value, new_value, changed_at, changed_by)
                     VALUES (?, ?, ?, 'reach_level', ?, ?, datetime('now'), ?)"
                )->execute([$type, $tid, $cr['id'], $old_dump, $new_dump, 'editor:' . $cr['editor_id']]);
            }
        }

        // Apply reach_class: replace
        if (isset($applied['reach_class'])) {
            $old = $cur['reach_class'];
            $new = $applied['reach_class'];
            if ($old !== $new) {
                $db->prepare('DELETE FROM reach_class WHERE reach_id = ?')->execute([$tid]);
                $ins = $db->prepare(
                    'INSERT INTO reach_class (reach_id, name) VALUES (?, ?)'
                );
                foreach ($new as $n) $ins->execute([$tid, $n]);

                $db->prepare(
                    "INSERT INTO edit_history
                     (target_type, target_id, change_request_id, field, old_value, new_value, changed_at, changed_by)
                     VALUES (?, ?, ?, 'reach_class', ?, ?, datetime('now'), ?)"
                )->execute([$type, $tid, $cr['id'],
                            implode(', ', $old),
                            implode(', ', $new),
                            'editor:' . $cr['editor_id']]);
            }
        }

        $db->prepare(
            "UPDATE change_request
             SET status = 'approved', reviewed_at = datetime('now'),
                 reviewed_by = ?, reviewer_note = ?, applied_json = ?
             WHERE id = ?"
        )->execute([
            $maint_id,
            $_POST['reviewer_note'] ?? null,
            json_encode($applied, JSON_UNESCAPED_SLASHES),
            $cr['id'],
        ]);

        $db->commit();
    } catch (Throwable $e) {
        $db->rollBack();
        error_log('review_approve: ' . $e->getMessage());
        return ['ok' => false, 'err' => 'apply failed: ' . $e->getMessage()];
    }
    return ['ok' => true];
}

function review_reject(PDO $db, array $cr, string $note, int $maint_id): void {
    $db->prepare(
        "UPDATE change_request
         SET status = 'rejected', reviewed_at = datetime('now'),
             reviewed_by = ?, reviewer_note = ?
         WHERE id = ?"
    )->execute([$maint_id, $note, $cr['id']]);
}

function review_notify_editor(PDO $db, array $cr, string $decision, string $note): void {
    $st = $db->prepare('SELECT email FROM editor WHERE id = ?');
    $st->execute([$cr['editor_id']]);
    $row = $st->fetch();
    if (!$row || empty($row['email'])) return;

    $target_label = $cr['subject'] ?: ($cr['target_type'] . ' #' . $cr['target_id']);
    send_email(
        (string)$row['email'],
        "[levels] your proposal was $decision",
        render_editor_decision_email($target_label, $decision, $note)
    );
}

// ---------------------------------------------------------------------------
// Controller
// ---------------------------------------------------------------------------

$cr_id = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT)
      ?: filter_input(INPUT_POST, 'id', FILTER_VALIDATE_INT);
$action = $_POST['action'] ?? null;
$flash = null;
$flash_err = null;

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    require_csrf();
    if (!$cr_id) {
        http_response_code(400);
        exit('Missing id');
    }
    $st = $db->prepare('SELECT * FROM change_request WHERE id = ?');
    $st->execute([$cr_id]);
    $cr = $st->fetch();
    if (!$cr) { http_response_code(404); exit('change_request not found'); }
    if ($cr['status'] !== 'pending') {
        $flash_err = 'This request has already been ' . $cr['status'] . '.';
    } elseif ($action === 'approve') {
        // Reconstruct the (possibly tweaked) applied payload from POST fields
        $payload = json_decode((string)$cr['payload_json'], true) ?: [];
        $applied = ['reach' => [], 'reach_level' => null, 'reach_class' => null];

        if (!empty($payload['reach'])) {
            foreach (array_keys($payload['reach']) as $f) {
                if (array_key_exists("reach_$f", $_POST)) {
                    $applied['reach'][$f] = trim((string)$_POST["reach_$f"]);
                } else {
                    $applied['reach'][$f] = $payload['reach'][$f];
                }
            }
        }
        if (isset($payload['reach_level']) && isset($_POST['levels_present'])) {
            $applied['reach_level'] = [];
            foreach (['low', 'okay', 'high'] as $tn) {
                $lo = trim((string)($_POST["level_{$tn}_low"]  ?? ''));
                $hi = trim((string)($_POST["level_{$tn}_high"] ?? ''));
                $dt = trim((string)($_POST["level_{$tn}_dt"]   ?? 'flow'));
                if ($lo === '' && $hi === '') continue;
                $applied['reach_level'][] = [
                    'level' => $tn,
                    'low'   => $lo !== '' ? (float)$lo : null,
                    'high'  => $hi !== '' ? (float)$hi : null,
                    'low_data_type' => $dt,
                ];
            }
        } else {
            unset($applied['reach_level']);
        }
        if (isset($payload['reach_class']) && isset($_POST['classes_present'])) {
            $raw = trim((string)($_POST['classes'] ?? ''));
            $applied['reach_class'] = $raw === ''
                ? []
                : array_values(array_filter(array_map('trim', explode(',', $raw))));
        } else {
            unset($applied['reach_class']);
        }

        $result = review_approve($db, $cr, $applied, (int)$maint['id']);
        if ($result['ok']) {
            review_notify_editor($db, $cr, 'approved', (string)($_POST['reviewer_note'] ?? ''));
            $flash = 'Approved and applied.';
        } else {
            $flash_err = $result['err'] ?? 'Apply failed.';
        }
    } elseif ($action === 'reject') {
        $note = trim((string)($_POST['reviewer_note'] ?? ''));
        review_reject($db, $cr, $note, (int)$maint['id']);
        review_notify_editor($db, $cr, 'rejected', $note);
        $flash = 'Rejected.';
    }
}

$csrf = htmlspecialchars(csrf_token());
header('Cache-Control: no-store');

// ---------------------------------------------------------------------------
// Detail view
// ---------------------------------------------------------------------------
if ($cr_id) {
    $st = $db->prepare(
        'SELECT cr.*, e.email AS editor_email, e.display_name AS editor_name, e.status AS editor_status
         FROM change_request cr JOIN editor e ON e.id = cr.editor_id
         WHERE cr.id = ?'
    );
    $st->execute([$cr_id]);
    $cr = $st->fetch();
    if (!$cr) { http_response_code(404); include_header('Not found'); echo '<p>Not found.</p>'; include_footer(); exit; }

    $payload = json_decode((string)$cr['payload_json'], true) ?: [];
    $applied = json_decode((string)($cr['applied_json'] ?? 'null'), true);
    $cur = $cr['target_type'] === 'reach' && $cr['target_id']
        ? review_load_target_state($db, 'reach', (int)$cr['target_id'])
        : null;

    include_header('Review: ' . ($cr['subject'] ?: 'change_request #' . $cr['id']));
    echo '<h2>Review: ' . htmlspecialchars((string)$cr['subject']) . '</h2>';
    if ($flash)     echo '<p style="padding:.5rem;background:#e8f4ea;border:1px solid #b7dcc0;border-radius:4px">' . htmlspecialchars($flash) . '</p>';
    if ($flash_err) echo '<p style="padding:.5rem;background:#fde8e8;border:1px solid #f5b5b5;border-radius:4px">' . htmlspecialchars($flash_err) . '</p>';

    echo '<table class="desc-table">';
    echo '<tr><td>From</td><td>' . htmlspecialchars((string)$cr['editor_email']) . ' (' . htmlspecialchars((string)$cr['editor_status']) . ')</td></tr>';
    echo '<tr><td>Submitted</td><td>' . htmlspecialchars((string)$cr['submitted_at']) . '</td></tr>';
    echo '<tr><td>Status</td><td>' . htmlspecialchars((string)$cr['status']) . '</td></tr>';
    if ($cr['target_type'] === 'reach' && $cr['target_id']) {
        echo '<tr><td>Reach</td><td><a href="/description.php?id=' . (int)$cr['target_id'] . '">description</a></td></tr>';
    }
    if ($cr['notes_to_maint']) {
        echo '<tr><td>Notes</td><td><pre style="white-space:pre-wrap;margin:0">'
           . htmlspecialchars((string)$cr['notes_to_maint']) . '</pre></td></tr>';
    }
    echo '</table>';

    if ($cr['status'] !== 'pending') {
        if ($applied) {
            echo '<h3>Applied payload</h3><pre style="white-space:pre-wrap">' . htmlspecialchars(json_encode($applied, JSON_PRETTY_PRINT)) . '</pre>';
        }
        echo '<p><a href="/review.php">Back to queue</a></p>';
        include_footer();
        exit;
    }

    // Editable approval form — user can tweak proposed values before approving.
    echo '<form method="POST" action="/review.php">';
    echo '<input type="hidden" name="csrf_token" value="' . $csrf . '">';
    echo '<input type="hidden" name="id" value="' . (int)$cr['id'] . '">';

    if (!empty($payload['reach'])) {
        echo '<h3>Reach field changes</h3>';
        echo '<table class="desc-table">';
        echo '<tr><th>Field</th><th>Current</th><th>Proposed (editable)</th></tr>';
        foreach ($payload['reach'] as $f => $v) {
            $cur_val = $cur ? (string)($cur['reach'][$f] ?? '') : '';
            $is_long = in_array($f, ['description', 'features'], true);
            echo '<tr><td>' . htmlspecialchars($f) . '</td>';
            echo '<td><pre style="white-space:pre-wrap;margin:0;max-width:30em">' . htmlspecialchars($cur_val) . '</pre></td>';
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

    if (isset($payload['reach_level'])) {
        echo '<h3>Flow levels (editable)</h3>';
        echo '<input type="hidden" name="levels_present" value="1">';
        echo '<table class="desc-table"><tr><th></th><th>Current low/high</th><th>Proposed low</th><th>Proposed high</th><th>Type</th></tr>';
        $proposed_by_tier = [];
        foreach ($payload['reach_level'] as $row) $proposed_by_tier[$row['level']] = $row;
        foreach (['low', 'okay', 'high'] as $tn) {
            $c = $cur && isset($cur['reach_level'][$tn]) ? $cur['reach_level'][$tn] : null;
            $p = $proposed_by_tier[$tn] ?? null;
            $cur_str = $c ? (($c['low'] ?? '') . ' — ' . ($c['high'] ?? '')) : '(none)';
            echo '<tr><td>' . $tn . '</td>';
            echo '<td>' . htmlspecialchars($cur_str) . '</td>';
            echo '<td><input type="number" step="any" name="level_' . $tn . '_low" value="' . htmlspecialchars((string)($p['low'] ?? '')) . '"></td>';
            echo '<td><input type="number" step="any" name="level_' . $tn . '_high" value="' . htmlspecialchars((string)($p['high'] ?? '')) . '"></td>';
            echo '<td><select name="level_' . $tn . '_dt">';
            $sel = $p['low_data_type'] ?? 'flow';
            foreach (['flow', 'gauge'] as $dt) {
                echo '<option value="' . $dt . '"' . ($sel === $dt ? ' selected' : '') . '>' . $dt . '</option>';
            }
            echo '</select></td></tr>';
        }
        echo '</table>';
    }

    if (isset($payload['reach_class'])) {
        echo '<h3>Classes</h3>';
        echo '<input type="hidden" name="classes_present" value="1">';
        $cur_cls = $cur ? implode(', ', $cur['reach_class']) : '';
        echo '<p>Current: <code>' . htmlspecialchars($cur_cls) . '</code></p>';
        echo '<input type="text" name="classes" value="' . htmlspecialchars(implode(', ', $payload['reach_class'])) . '" style="width:100%">';
    }

    echo '<h3 style="margin-top:1rem">Decision</h3>';
    echo '<label>Reviewer note (included in email to editor)</label>';
    echo '<textarea name="reviewer_note" style="width:100%;min-height:4em"></textarea>';
    echo '<p style="margin-top:.5rem">';
    echo '<button type="submit" name="action" value="approve">Approve and apply</button>';
    echo ' <button type="submit" name="action" value="reject" '
       . 'onclick="return confirm(\'Reject this proposal?\')">Reject</button>';
    echo ' <a href="/review.php" style="margin-left:1rem">Back to queue</a>';
    echo '</p>';
    echo '</form>';

    include_footer();
    exit;
}

// ---------------------------------------------------------------------------
// List view
// ---------------------------------------------------------------------------
$q_status = $_GET['status'] ?? 'pending';
if (!in_array($q_status, ['pending', 'approved', 'rejected', 'all'], true)) {
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
$rows = $db->prepare($sql);
$rows->execute($params);
$rows = $rows->fetchAll();

include_header('Review queue');
echo '<h2>Review queue</h2>';
if ($flash)     echo '<p style="padding:.5rem;background:#e8f4ea;border:1px solid #b7dcc0;border-radius:4px">' . htmlspecialchars($flash) . '</p>';
if ($flash_err) echo '<p style="padding:.5rem;background:#fde8e8;border:1px solid #f5b5b5;border-radius:4px">' . htmlspecialchars($flash_err) . '</p>';

echo '<p style="font-size:.85rem">Status: ';
foreach (['pending', 'approved', 'rejected', 'all'] as $s) {
    $cls = $s === $q_status ? ' style="font-weight:700"' : '';
    echo '<a href="/review.php?status=' . $s . '"' . $cls . '>' . $s . '</a> &nbsp;';
}
echo '<a href="/admin.php" style="float:right">Admin</a>';
echo '</p>';

if (!$rows) {
    echo '<p>No proposals.</p>';
} else {
    echo '<table class="desc-table">';
    echo '<tr><th>Submitted</th><th>From</th><th>Target</th><th>Subject</th><th>Status</th></tr>';
    foreach ($rows as $r) {
        $tgt = $r['target_type'];
        if ($r['target_id']) $tgt .= ' #' . $r['target_id'];
        echo '<tr>'
           . '<td>' . htmlspecialchars((string)$r['submitted_at']) . '</td>'
           . '<td>' . htmlspecialchars((string)$r['editor_email']) . ' <span style="color:var(--c-text-muted);font-size:.75rem">(' . htmlspecialchars((string)$r['editor_status']) . ')</span></td>'
           . '<td>' . htmlspecialchars($tgt) . '</td>'
           . '<td><a href="/review.php?id=' . (int)$r['id'] . '">'
                . htmlspecialchars((string)$r['subject']) . '</a></td>'
           . '<td>' . htmlspecialchars((string)$r['status']) . '</td>'
           . '</tr>';
    }
    echo '</table>';
}

include_footer();
