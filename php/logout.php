<?php
declare(strict_types=1);
/**
 * Log out — revoke current session and clear the cookie.
 *
 * GET  /logout.php     Show confirmation form
 * POST /logout.php     Revoke + redirect home
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

require_editor_feature();

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    require_csrf();
    clear_editor_session();
    header('Location: /');
    exit;
}

$csrf = htmlspecialchars(csrf_token());
header('Cache-Control: no-store');
include_header('Log out');
?>
<h2>Log out</h2>
<p>End your session on this browser?</p>
<form method="POST" action="/logout.php">
  <input type="hidden" name="csrf_token" value="<?= $csrf ?>">
  <button type="submit">Yes, log me out</button>
  <a href="/" style="margin-left:1rem">Cancel</a>
</form>
<?php include_footer(); ?>
