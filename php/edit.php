<?php
declare(strict_types=1);
/**
 * Reach editing form + submission — maintainer-only.
 *
 * GET  /edit.php?id=<reach_id>   — show the edit form
 * POST /edit.php?id=<reach_id>   — save changes
 *
 * Auth: ed_sess editor-session cookie + ed_csrf double-submit cookie
 * (same pattern as /review.php). A signed-in editor without
 * status='maintainer' gets 403; anonymous visitors get bounced to
 * /login.php via require_editor(). propose.php routes maintainers here
 * automatically so there's a single canonical direct-edit path.
 *
 * Every field that actually changes is logged to edit_history with
 * changed_by='maintainer:<editor_id>' and change_request_id=NULL — the
 * same schema review.php uses when approving an editor proposal.
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

$maintainer = require_maintainer();

$id = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT)
    ?: filter_input(INPUT_POST, 'reach_id', FILTER_VALIDATE_INT);
if (!$id) { http_response_code(400); exit('Missing id parameter'); }

$db = get_db();

$reach = get_reach_or_404($id);
$name = $reach['display_name'] ?: $reach['name'];

$editable_fields = [
    'display_name', 'sort_name', 'description', 'difficulties',
    'basin', 'region', 'length', 'gradient', 'elevation_lost',
    'season', 'scenery', 'features', 'remoteness', 'nature',
    'watershed_type', 'optimal_flow', 'notes',
];

$numeric_fields = ['length', 'gradient', 'elevation_lost', 'optimal_flow'];
$textarea_fields = ['description', 'difficulties', 'features', 'notes'];

// -----------------------------------------------------------------------
// POST — apply changes + log diff to edit_history
// -----------------------------------------------------------------------
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    require_csrf();

    // Compare each submitted editable field against the current reach row;
    // only changed values get into the UPDATE and the audit log. Empty
    // submissions are skipped (matches the legacy behavior — clearing a
    // field requires a separate flow that this form doesn't offer yet).
    $sets = [];
    $params = [];
    $changes = [];  // [field => ['old' => ..., 'new' => ...]]
    foreach ($editable_fields as $field) {
        if (!isset($_POST[$field])) continue;
        $val = trim($_POST[$field]);
        if ($val === '') continue;

        if (in_array($field, $numeric_fields, true)) {
            if (!is_numeric($val)) continue;
            $val = (float)$val;
        }

        $old = $reach[$field] ?? null;
        // Coerce both sides to string for comparison so numeric-vs-string
        // differences don't log as no-op changes.
        if ((string)$old === (string)$val) continue;

        $sets[] = "$field = ?";
        $params[] = $val;
        $changes[$field] = ['old' => $old, 'new' => $val];
    }

    if ($changes) {
        $db->beginTransaction();
        try {
            $sets[] = "updated_at = datetime('now')";
            $params[] = $id;
            $db->prepare('UPDATE reach SET ' . implode(', ', $sets) . ' WHERE id = ?')
                ->execute($params);

            $hist = $db->prepare(
                "INSERT INTO edit_history
                   (target_type, target_id, change_request_id, field, old_value, new_value,
                    changed_at, changed_by)
                 VALUES ('reach', ?, NULL, ?, ?, ?, datetime('now'), ?)"
            );
            $changed_by = 'maintainer:' . (int)$maintainer['id'];
            foreach ($changes as $field => $pair) {
                $hist->execute([
                    $id,
                    $field,
                    $pair['old'] === null ? null : (string)$pair['old'],
                    $pair['new'] === null ? null : (string)$pair['new'],
                    $changed_by,
                ]);
            }
            $db->commit();
        } catch (Throwable $e) {
            $db->rollBack();
            error_log('edit.php: apply failed: ' . $e->getMessage());
            http_response_code(500);
            exit('Save failed — see server log.');
        }
    }

    header('Cache-Control: no-cache');
    include_header('Changes Saved', '', '', '', ['type' => 'reach', 'id' => $id]);
    echo '<h2>Changes Saved</h2>';
    if ($changes) {
        $n = count($changes);
        $fields = implode(', ', array_keys($changes));
        echo '<p>Saved ' . $n . ' field' . ($n === 1 ? '' : 's')
           . ' for <strong>' . htmlspecialchars($name) . '</strong>: '
           . '<code>' . htmlspecialchars($fields) . '</code></p>';
    } else {
        echo '<p>No changes to save for <strong>' . htmlspecialchars($name) . '</strong>.</p>';
    }
    echo '<p><a href="/description.php?id=' . $id . '">View description</a>';
    echo ' | <a href="/index.html">Back to main page</a></p>';
    include_footer();
    exit;
}

// -----------------------------------------------------------------------
// GET — show the form
// -----------------------------------------------------------------------
header('Cache-Control: no-cache');
include_header("Edit $name", '', '', '', ['type' => 'reach', 'id' => $id]);

echo '<h2>Edit: ' . htmlspecialchars($name) . '</h2>';
echo '<form method="POST" action="/edit.php?id=' . $id . '" class="edit-form">';
echo '<input type="hidden" name="reach_id" value="' . $id . '">';
echo '<input type="hidden" name="csrf_token" value="' . htmlspecialchars(csrf_token()) . '">';

foreach ($editable_fields as $field) {
    $val = htmlspecialchars((string)($reach[$field] ?? ''));
    $label = ucwords(str_replace('_', ' ', $field));
    echo "<label>$label</label>";

    if (in_array($field, $textarea_fields, true)) {
        echo "<textarea name=\"$field\">$val</textarea>";
    } else {
        echo "<input type=\"text\" name=\"$field\" value=\"$val\">";
    }
}

echo '<button type="submit">Save Changes</button>';
echo '</form>';
echo '<p style="margin-top:1rem"><a href="/description.php?id=' . $id . '">Cancel</a></p>';

include_footer();
