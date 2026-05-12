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
require_once __DIR__ . '/includes/sanity.php';
require_once __DIR__ . '/includes/review_logic.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

require_editor_feature();
$maint = require_maintainer();
$db = get_db();

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
        $applied = ['reach' => [], 'reach_class' => null];

        if (!empty($payload['reach'])) {
            foreach (array_keys($payload['reach']) as $f) {
                if (array_key_exists("reach_$f", $_POST)) {
                    $applied['reach'][$f] = trim((string)$_POST["reach_$f"]);
                } else {
                    $applied['reach'][$f] = $payload['reach'][$f];
                }
            }
        }
        if (isset($payload['reach_class']) && isset($_POST['classes_present'])) {
            $raw = trim((string)($_POST['classes'] ?? ''));
            $names = $raw === '' ? [] : array_values(array_filter(array_map('trim', explode(',', $raw))));
            $lo = trim((string)($_POST['flow_low']       ?? ''));
            $hi = trim((string)($_POST['flow_high']      ?? ''));
            $dt = trim((string)($_POST['flow_data_type'] ?? 'flow'));
            $applied['reach_class'] = [
                'names' => $names,
                'range' => [
                    'low'       => $lo !== '' ? (float)$lo : null,
                    'high'      => $hi !== '' ? (float)$hi : null,
                    'data_type' => $dt ?: 'flow',
                ],
            ];
        } else {
            unset($applied['reach_class']);
        }

        $approve_note = trim((string)($_POST['reviewer_note'] ?? ''));
        $result = review_approve($db, $cr, $applied, (int)$maint['id'], $approve_note);
        if ($result['ok']) {
            review_notify_editor($db, $cr, 'approved', $approve_note);
            $flash = 'Approved and applied.';
        } else {
            $flash_err = $result['err'] ?? 'Apply failed.';
        }
    } elseif ($action === 'reject') {
        $note = trim((string)($_POST['reviewer_note'] ?? ''));
        if (review_reject($db, $cr, $note, (int)$maint['id'])) {
            review_notify_editor($db, $cr, 'rejected', $note);
            $flash = 'Rejected.';
        } else {
            $flash_err = 'Already reviewed by another maintainer.';
        }
    } elseif ($action === 'reply') {
        $note = trim((string)($_POST['reviewer_note'] ?? ''));
        if ($note === '') {
            $flash_err = 'Reply cannot be empty.';
        } else {
            review_send_reply($db, $cr, $note, (int)$maint['id']);
            $flash = 'Reply sent — proposal kept pending.';
        }
    } elseif ($action === 'reply_and_close') {
        $note = trim((string)($_POST['reviewer_note'] ?? ''));
        if ($note === '') {
            $flash_err = 'Reply cannot be empty.';
        } elseif (review_reply_and_close($db, $cr, $note, (int)$maint['id'])) {
            $flash = 'Reply sent and proposal marked resolved.';
        } else {
            $flash_err = 'Already reviewed by another maintainer.';
        }
    } elseif ($action === 'resolve') {
        $note = trim((string)($_POST['reviewer_note'] ?? ''));
        if (review_resolve($db, $cr, $note, (int)$maint['id'])) {
            review_notify_editor($db, $cr, 'resolved', $note);
            $flash = 'Marked resolved.';
        } else {
            $flash_err = 'Already reviewed by another maintainer.';
        }
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
    if (!$cr) {
        require_once __DIR__ . '/includes/error.php';
        render_error_page(
            404,
            'Not found',
            '<p>No change request with id ' . (int)$cr_id . ' exists.</p>'
            . '<p><a href="/review.php">Back to the review queue</a></p>'
        );
    }

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
    if (!empty($cr['source_url'])) {
        $src = (string)$cr['source_url'];
        echo '<tr><td>Page</td><td><a href="' . htmlspecialchars($src) . '">'
           . htmlspecialchars($src) . '</a></td></tr>';
    }
    echo '<tr><td>Status</td><td>' . htmlspecialchars((string)$cr['status']) . '</td></tr>';
    if ($cr['target_type'] === 'reach' && $cr['target_id']) {
        echo '<tr><td>Reach</td><td><a href="/description.php?id=' . (int)$cr['target_id'] . '">description</a></td></tr>';
    }
    if (!empty($payload['body'])) {
        echo '<tr><td>Message</td><td><pre style="white-space:pre-wrap;margin:0">'
           . htmlspecialchars((string)$payload['body']) . '</pre></td></tr>';
    }
    if ($cr['notes_to_maint']) {
        echo '<tr><td>Notes</td><td><pre style="white-space:pre-wrap;margin:0">'
           . htmlspecialchars((string)$cr['notes_to_maint']) . '</pre></td></tr>';
    }
    echo '</table>';

    if ($cr['status'] !== 'pending') {
        if (!empty($cr['reviewer_note'])) {
            echo '<h3>Maintainer notes</h3>';
            echo '<pre style="white-space:pre-wrap;background:#f6f8fa;border:1px solid #e1e4e8;border-radius:4px;padding:.5rem">'
               . htmlspecialchars((string)$cr['reviewer_note']) . '</pre>';
        }
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

    if (isset($payload['reach_class'])) {
        echo '<h3>Classes and flow range (editable)</h3>';
        echo '<input type="hidden" name="classes_present" value="1">';
        $cur_names = $cur['reach_class']['names'] ?? [];
        $cur_range = $cur['reach_class']['range'] ?? ['low'=>null, 'high'=>null, 'data_type'=>'flow'];
        $p_names = $payload['reach_class']['names'] ?? [];
        $p_range = $payload['reach_class']['range'] ?? ['low'=>null, 'high'=>null, 'data_type'=>'flow'];
        echo '<p>Current classes: <code>' . htmlspecialchars(implode(', ', $cur_names) ?: '(none)') . '</code></p>';
        $cur_range_str = ($cur_range['low'] ?? '-') . ' to ' . ($cur_range['high'] ?? '-')
                       . ' ' . ($cur_range['data_type'] ?? 'flow');
        echo '<p>Current range: <code>' . htmlspecialchars($cur_range_str) . '</code></p>';
        echo '<label>Proposed classes (comma-separated)</label>';
        echo '<input type="text" name="classes" value="' . htmlspecialchars(implode(', ', $p_names)) . '" style="width:100%">';
        echo '<table style="margin-top:.5rem"><tr><th>Low</th><th>High</th><th>Type</th></tr><tr>';
        echo '<td><input type="number" step="any" name="flow_low" value="'  . htmlspecialchars((string)($p_range['low']  ?? '')) . '"></td>';
        echo '<td><input type="number" step="any" name="flow_high" value="' . htmlspecialchars((string)($p_range['high'] ?? '')) . '"></td>';
        echo '<td><select name="flow_data_type">';
        $sel = $p_range['data_type'] ?? 'flow';
        foreach (['flow', 'gauge'] as $dt) {
            echo '<option value="' . $dt . '"' . ($sel === $dt ? ' selected' : '') . '>' . $dt . '</option>';
        }
        echo '</select></td></tr></table>';
    }

    echo '<h3 style="margin-top:1rem">Decision</h3>';
    echo '<label>Reviewer note / reply (included in the email to the editor)</label>';
    echo '<textarea name="reviewer_note" style="width:100%;min-height:4em"></textarea>';
    if ($cr['reviewer_note']) {
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

    include_footer();
    exit;
}

// ---------------------------------------------------------------------------
// List view
// ---------------------------------------------------------------------------
$q_status = $_GET['status'] ?? 'pending';
if (!in_array($q_status, ['pending', 'approved', 'rejected', 'resolved', 'all'], true)) {
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
foreach (['pending', 'approved', 'rejected', 'resolved', 'all'] as $s) {
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
