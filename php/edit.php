<?php
/**
 * Section editing form + submission.
 *
 * GET  /edit.php?id=<section_id>         — show form
 * POST /edit.php?id=<section_id>         — save changes
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

$edit_user = getenv('EDIT_USER') ?: 'admin';
$edit_password = getenv('EDIT_PASSWORD');
if ($edit_password) {
    if (!isset($_SERVER['PHP_AUTH_USER'])
        || $_SERVER['PHP_AUTH_USER'] !== $edit_user
        || $_SERVER['PHP_AUTH_PW'] !== $edit_password) {
        header('WWW-Authenticate: Basic realm="Edit Section"');
        http_response_code(401);
        exit('Unauthorized');
    }
}

$id = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT)
    ?: filter_input(INPUT_POST, 'section_id', FILTER_VALIDATE_INT);
if (!$id) { http_response_code(400); exit('Missing id parameter'); }

$db = get_db();

$stmt = $db->prepare('SELECT * FROM section WHERE id = ?');
$stmt->execute([$id]);
$section = $stmt->fetch();
if (!$section) { http_response_code(404); exit('Section not found'); }

$name = $section['display_name'] ?: $section['name'];

$editable_fields = [
    'display_name', 'sort_name', 'description', 'difficulties',
    'basin', 'region', 'length', 'gradient', 'elevation_lost',
    'season', 'scenery', 'features', 'remoteness', 'nature',
    'watershed_type', 'optimal_flow', 'notes',
];

$numeric_fields = ['length', 'gradient', 'elevation_lost', 'optimal_flow'];
$textarea_fields = ['description', 'difficulties', 'features', 'notes'];

// Handle POST submission
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $sets = [];
    $params = [];
    foreach ($editable_fields as $field) {
        if (!isset($_POST[$field])) continue;
        $val = trim($_POST[$field]);
        if ($val === '') continue;

        if (in_array($field, $numeric_fields)) {
            if (!is_numeric($val)) continue;
            $val = (float)$val;
        }

        $sets[] = "$field = ?";
        $params[] = $val;
    }

    if ($sets) {
        $params[] = $id;
        $sql = 'UPDATE section SET ' . implode(', ', $sets) . ' WHERE id = ?';
        $db->prepare($sql)->execute($params);
    }

    header('Cache-Control: no-cache');
    include_header('Changes Saved');
    echo '<h2>Changes Saved</h2>';
    echo '<p>Your changes for <strong>' . htmlspecialchars($name) . '</strong> have been saved.</p>';
    echo '<p><a href="/description.php?id=' . $id . '">View description</a>';
    echo ' | <a href="/index.html">Back to main page</a></p>';
    include_footer();
    exit;
}

// Show edit form
header('Cache-Control: no-cache');
include_header("Edit $name");

echo '<h2>Edit: ' . htmlspecialchars($name) . '</h2>';
echo '<form method="POST" action="/edit.php?id=' . $id . '" class="edit-form">';
echo '<input type="hidden" name="section_id" value="' . $id . '">';

foreach ($editable_fields as $field) {
    $val = htmlspecialchars((string)($section[$field] ?? ''));
    $label = ucwords(str_replace('_', ' ', $field));
    echo "<label>$label</label>";

    if (in_array($field, $textarea_fields)) {
        echo "<textarea name=\"$field\">$val</textarea>";
    } else {
        echo "<input type=\"text\" name=\"$field\" value=\"$val\">";
    }
}

echo '<button type="submit">Save Changes</button>';
echo '</form>';
echo '<p style="margin-top:1rem"><a href="/description.php?id=' . $id . '">Cancel</a></p>';

include_footer();
