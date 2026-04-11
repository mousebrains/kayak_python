<?php
/**
 * Reach editing form + submission.
 *
 * GET  /edit.php?id=<reach_id>         — show form
 * POST /edit.php?id=<reach_id>         — save changes
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

// Require HTTP Basic Auth — EDIT_PASSWORD must be set or editing is disabled
$edit_user = ($_SERVER['EDIT_USER'] ?? getenv('EDIT_USER')) ?: 'admin';
$edit_password = $_SERVER['EDIT_PASSWORD'] ?? getenv('EDIT_PASSWORD');
if (!$edit_password) {
    http_response_code(403);
    exit('Editing is disabled (EDIT_PASSWORD not configured)');
}
if (!isset($_SERVER['PHP_AUTH_USER'])
    || $_SERVER['PHP_AUTH_USER'] !== $edit_user
    || $_SERVER['PHP_AUTH_PW'] !== $edit_password) {
    header('WWW-Authenticate: Basic realm="Edit Reach"');
    http_response_code(401);
    exit('Unauthorized');
}

// CSRF protection — generate/verify a per-session token
session_set_cookie_params([
    'lifetime' => 0,
    'path'     => '/edit.php',
    'secure'   => !empty($_SERVER['HTTPS']),
    'httponly'  => true,
    'samesite' => 'Strict',
]);
ini_set('session.use_strict_mode', '1');
session_start();
if (empty($_SESSION['csrf_token'])) {
    $_SESSION['csrf_token'] = bin2hex(random_bytes(32));
}
$csrf_token = $_SESSION['csrf_token'];

$id = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT)
    ?: filter_input(INPUT_POST, 'reach_id', FILTER_VALIDATE_INT);
if (!$id) { http_response_code(400); exit('Missing id parameter'); }

$db = get_db();

$stmt = $db->prepare('SELECT * FROM reach WHERE id = ?');
$stmt->execute([$id]);
$reach = $stmt->fetch();
if (!$reach) { http_response_code(404); exit('Reach not found'); }

$name = $reach['display_name'] ?: $reach['name'];

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
    if (!isset($_POST['csrf_token']) || !hash_equals($csrf_token, $_POST['csrf_token'])) {
        http_response_code(403);
        exit('Invalid CSRF token');
    }
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
        $sql = 'UPDATE reach SET ' . implode(', ', $sets) . ' WHERE id = ?';
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
echo '<input type="hidden" name="reach_id" value="' . $id . '">';
echo '<input type="hidden" name="csrf_token" value="' . htmlspecialchars($csrf_token) . '">';

foreach ($editable_fields as $field) {
    $val = htmlspecialchars((string)($reach[$field] ?? ''));
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
