<?php
declare(strict_types=1);

require_once __DIR__ . '/auth.php';

function include_footer(): void {
    $feature = editor_feature_enabled();
    $ed      = $feature ? current_editor() : null;

    $items = [];
    if ($feature) {
        if ($ed) {
            $items[] = is_maintainer($ed)
                ? '<a href="/admin.php">Admin</a>'
                : '<a href="/account.php">Account</a>';
            $items[] = '<a href="/logout.php">Log out</a>';
        } else {
            $items[] = '<a href="/login.php">Login</a>';
        }
        $items[] = '<a href="/comment.php">Comment</a>';
    }
    $items[] = '<a href="/about.php">About</a>';
    $items[] = '<a href="/contact.php">Contact</a>';
    $items[] = '<a href="/disclaimer.php">Disclaimer</a>';
    $items[] = '<a href="/privacy.php">Privacy Policy</a>';
    $links = implode(' &middot; ', $items);

    echo <<<HTML
</main>
<footer>
<p>$links</p>
<p>Data sourced from USGS, NOAA, USACE, USBR, and other government agencies.</p>
<p>Code: <a href="/LICENSE.txt">GPL v3</a> &middot; Data: <a href="/LICENSE-DATA.txt">CC BY-NC 4.0</a></p>
</footer>
<script src="/static/levels.js" defer></script>
<script src="/static/plot-hover.js" defer></script>
</body>
</html>
HTML;
}
