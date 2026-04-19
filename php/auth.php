<?php
declare(strict_types=1);
/**
 * Magic-link consumer.
 *
 * GET /auth.php?t=<token>[&next=/path]    Verify token, create session, redirect
 *
 * One-shot tokens are marked used immediately. A replay (browser back button,
 * email forwarded) lands on a friendly "link expired" page.
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

require_editor_feature();

$tok = (string)($_GET['t'] ?? '');
$next = safe_next_url($_GET['next'] ?? null);

$result = $tok !== '' ? consume_magic_link($tok) : null;

if ($result === null) {
    http_response_code(400);
    header('Cache-Control: no-store');
    include_header('Login link expired');
    echo '<h2>That login link is not valid.</h2>';
    echo '<p>Links expire after 30 minutes and can only be used once. '
       . 'Try requesting a fresh one from the login page.</p>';
    echo '<p><a href="/login.php">Back to sign in</a></p>';
    include_footer();
    exit;
}

// Token valid — establish a session.
set_editor_session($result['editor_id']);

$redirect = safe_next_url($result['next_url'] ?? $next);
header("Location: $redirect");
exit;
