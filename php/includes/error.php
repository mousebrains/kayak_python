<?php
declare(strict_types=1);
/**
 * Friendly error pages rendered through the site chrome.
 *
 * Use instead of `http_response_code(N); exit('text')` so the visitor lands
 * on a real page with the header, nav, and footer — not a spartan plain-text
 * body. Callers are the auth gates (require_editor_feature,
 * require_maintainer) and the 404 primitives (get_reach_or_404).
 */

require_once __DIR__ . '/header.php';
require_once __DIR__ . '/footer.php';

/**
 * Render an error page with status $code and terminate the request.
 *
 * $message_html is inserted verbatim — callers must escape any interpolated
 * user data themselves.
 */
function render_error_page(int $code, string $title, string $message_html): void {
    http_response_code($code);
    header('Cache-Control: no-store');
    include_header($title);
    echo '<h2>' . htmlspecialchars($title) . '</h2>';
    echo $message_html;
    echo '<p style="margin-top:1.5rem"><a href="/">&larr; Back to river levels</a></p>';
    include_footer();
    exit;
}
