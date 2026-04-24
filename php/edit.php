<?php
declare(strict_types=1);
/**
 * Reach/gauge editing form + submission — maintainer-only.
 *
 * GET  /edit.php?id=<id>[&type=reach|gauge]   — show the edit form
 * POST /edit.php?id=<id>[&type=reach|gauge]   — save changes
 *
 * Auth: ed_sess editor-session cookie + ed_csrf double-submit cookie
 * (same pattern as /review.php). A signed-in editor without
 * status='maintainer' gets 403; anonymous visitors get bounced to
 * /login.php via require_maintainer(). propose.php routes maintainers
 * here automatically for the reach path so there's a single canonical
 * direct-edit path.
 *
 * Every field that actually changes is logged to edit_history with
 * changed_by='maintainer:<editor_id>' and change_request_id=NULL — the
 * same schema review.php uses when approving an editor proposal.
 *
 * Type defaults to 'reach' for backward compatibility; 'gauge' edits
 * gauge-metadata fields (location, coordinates, elevation, etc.).
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

$maintainer = require_maintainer();

$type = $_GET['type'] ?? $_POST['target_type'] ?? 'reach';
if (!in_array($type, ['reach', 'gauge'], true)) {
    http_response_code(400);
    exit('Unsupported edit target type');
}

$id = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT)
    ?: filter_input(INPUT_POST, 'reach_id', FILTER_VALIDATE_INT)
    ?: filter_input(INPUT_POST, 'gauge_id', FILTER_VALIDATE_INT);
if (!$id) { http_response_code(400); exit('Missing id parameter'); }

$db = get_db();

if ($type === 'reach') {
    $row = get_reach_or_404($id);
    $name = $row['display_name'] ?: $row['name'];
    $table = 'reach';
    $editable_fields = [
        'display_name', 'sort_name', 'description', 'difficulties',
        'basin', 'region', 'length', 'gradient', 'elevation_lost',
        'season', 'scenery', 'features', 'remoteness', 'nature',
        'watershed_type', 'optimal_flow', 'notes',
    ];
    $numeric_fields = ['length', 'gradient', 'elevation_lost', 'optimal_flow'];
    $textarea_fields = ['description', 'difficulties', 'features', 'notes'];
    $back_url = "/description.php?id=$id";
    $back_label = 'View description';
} else { // gauge
    $stmt = $db->prepare('SELECT * FROM gauge WHERE id = ?');
    $stmt->execute([$id]);
    $row = $stmt->fetch();
    if (!$row) { http_response_code(404); exit('Gauge not found'); }
    $name = $row['name'];
    $table = 'gauge';
    $editable_fields = [
        'name', 'location',
        'latitude', 'longitude', 'elevation', 'drainage_area',
        'bank_full', 'flood_stage', 'huc',
        'station_id', 'usgs_id', 'cbtt_id', 'geos_id', 'nws_id', 'nwsli_id', 'snotel_id',
    ];
    $numeric_fields = [
        'latitude', 'longitude', 'elevation', 'drainage_area',
        'bank_full', 'flood_stage',
    ];
    $textarea_fields = [];
    $back_url = "/gauge.php?id=$id";
    $back_label = 'View gauge';
}

// -----------------------------------------------------------------------
// POST — apply changes + log diff to edit_history
// -----------------------------------------------------------------------
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    require_csrf();

    // Compare each submitted editable field against the current row; only
    // changed values get into the UPDATE and the audit log. Empty
    // submissions are skipped (matches the legacy behavior — clearing a
    // field requires a separate flow that this form doesn't offer yet).
    $sets = [];
    $params = [];
    $changes = [];
    foreach ($editable_fields as $field) {
        if (!isset($_POST[$field])) continue;
        $val = trim($_POST[$field]);
        if ($val === '') continue;

        if (in_array($field, $numeric_fields, true)) {
            if (!is_numeric($val)) continue;
            $val = (float)$val;
        }

        $old = $row[$field] ?? null;
        if ((string)$old === (string)$val) continue;

        $sets[] = "$field = ?";
        $params[] = $val;
        $changes[$field] = ['old' => $old, 'new' => $val];
    }

    if ($changes) {
        $db->beginTransaction();
        try {
            if ($table === 'reach') {
                $sets[] = "updated_at = datetime('now')";
            }
            $params[] = $id;
            $db->prepare('UPDATE ' . $table . ' SET ' . implode(', ', $sets) . ' WHERE id = ?')
                ->execute($params);

            $hist = $db->prepare(
                "INSERT INTO edit_history
                   (target_type, target_id, change_request_id, field, old_value, new_value,
                    changed_at, changed_by)
                 VALUES (?, ?, NULL, ?, ?, ?, datetime('now'), ?)"
            );
            $changed_by = 'maintainer:' . (int)$maintainer['id'];
            foreach ($changes as $field => $pair) {
                $hist->execute([
                    $type,
                    $id,
                    $field,
                    $pair['old'] === null ? null : (string)$pair['old'],
                    (string)$pair['new'],
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
    include_header('Changes Saved', '', '', '', ['type' => $type, 'id' => $id]);
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
    echo '<p><a href="' . htmlspecialchars($back_url) . '">' . htmlspecialchars($back_label) . '</a>';
    echo ' | <a href="/index.html">Back to main page</a></p>';
    include_footer();
    exit;
}

// -----------------------------------------------------------------------
// GET — show the form
// -----------------------------------------------------------------------
header('Cache-Control: no-cache');
include_header("Edit $name", '', '', '', ['type' => $type, 'id' => $id]);

echo '<h2>Edit: ' . htmlspecialchars($name) . '</h2>';
$action = '/edit.php?id=' . $id . ($type === 'gauge' ? '&amp;type=gauge' : '');
echo '<form method="POST" action="' . $action . '" class="edit-form">';
echo '<input type="hidden" name="target_type" value="' . htmlspecialchars($type) . '">';
$id_field = $type === 'gauge' ? 'gauge_id' : 'reach_id';
echo '<input type="hidden" name="' . $id_field . '" value="' . $id . '">';
echo '<input type="hidden" name="csrf_token" value="' . htmlspecialchars(csrf_token()) . '">';

foreach ($editable_fields as $field) {
    $val = htmlspecialchars((string)($row[$field] ?? ''));
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
echo '<p style="margin-top:1rem"><a href="' . htmlspecialchars($back_url) . '">Cancel</a></p>';

include_footer();
