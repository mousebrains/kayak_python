<?php
declare(strict_types=1);
/**
 * /_internal/status — serves the nightly-rendered operator status page.
 *
 * The HTML body is rendered out-of-band by `levels status` (Python; via
 * the kayak-status.timer at 03:30 daily) into /home/pat/var/status.html.
 * This wrapper just enforces maintainer auth and streams the cached file.
 *
 * The cache lives outside the document root so nginx cannot serve it
 * directly — only this PHP file, behind require_maintainer(), can.
 *
 * Companion to /_internal/index.php (the live data dashboard) and
 * /status.json (the public health snapshot at php/status.php).
 */

require_once __DIR__ . '/../includes/auth.php';

require_maintainer();

header('Content-Type: text/html; charset=utf-8');
header('X-Robots-Tag: noindex, nofollow');
header('Cache-Control: no-store, private');

$cached = '/home/pat/var/status.html';
if (!is_readable($cached)) {
    http_response_code(503);
    echo "<!doctype html>\n<html><head><meta charset=\"utf-8\">"
       . "<title>Status — not yet generated</title></head><body>\n"
       . "<h1>Status page not yet generated</h1>\n"
       . "<p>Run <code>sudo systemctl start kayak-status.service</code> "
       . "to render, or wait until the next 03:30 fire of "
       . "<code>kayak-status.timer</code>.</p>\n"
       . "</body></html>\n";
    return;
}

readfile($cached);
