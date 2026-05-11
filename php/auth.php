<?php
declare(strict_types=1);
/**
 * Magic-link consumer.
 *
 * GET  /auth.php?t=<token>[&next=/path]   Renders an interstitial "Click to
 *                                         sign in" form. Token is NOT consumed.
 * POST /auth.php                          Consumes token + creates session +
 *                                         redirects to next.
 *
 * The GET → POST split defends against email-scanner URL prefetch (Outlook
 * Defender, Proofpoint, etc.) burning the one-shot token before the actual
 * user clicks. Prefetchers fetch GET, see the form, leave the token alone.
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

require_editor_feature();
header('Cache-Control: no-store');

function _render_expired(): void {
    http_response_code(400);
    include_header('Login link expired');
    echo '<h2>That login link is not valid.</h2>';
    echo '<p>Links expire after 30 minutes and can only be used once. '
       . 'Try requesting a fresh one from the login page.</p>';
    echo '<p><a href="/login.php">Back to sign in</a></p>';
    include_footer();
}

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    require_csrf();
    $tok    = (string)($_POST['t'] ?? '');
    $next   = safe_next_url($_POST['next'] ?? null);
    $result = $tok !== '' ? consume_magic_link($tok) : null;

    if ($result === null) {
        _render_expired();
        exit;
    }

    set_editor_session($result['editor_id']);
    $redirect = safe_next_url($result['next_url'] ?? $next);
    header("Location: $redirect");
    exit;
}

// GET: peek the token (no consume), render an interstitial form so the
// real user has to click to complete sign-in.
$tok  = (string)($_GET['t'] ?? '');
$next = safe_next_url($_GET['next'] ?? null);

if (!peek_magic_link($tok)) {
    _render_expired();
    exit;
}

$csrf     = htmlspecialchars(csrf_token());
$tok_esc  = htmlspecialchars($tok);
$next_esc = htmlspecialchars($next);

include_header('Confirm sign in');
echo '<h2>Sign in</h2>';
echo '<p>Click the button below to complete sign-in. '
   . 'This extra step protects your account when email security scanners '
   . 'fetch links automatically.</p>';
echo '<form method="POST" action="/auth.php">';
echo '  <input type="hidden" name="csrf_token" value="' . $csrf . '">';
echo '  <input type="hidden" name="t" value="' . $tok_esc . '">';
echo '  <input type="hidden" name="next" value="' . $next_esc . '">';
echo '  <button type="submit" class="primary-button">Sign in</button>';
echo '</form>';
include_footer();
