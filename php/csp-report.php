<?php
declare(strict_types=1);
/**
 * CSP violation report sink.
 *
 * Browsers POST CSP violation reports here when the kayak vhost's
 * `Content-Security-Policy` header carries `report-uri /csp-report.php`.
 * Both CSP v1 payloads (`{"csp-report": {...}}`) and Reporting API v2
 * payloads (arrays of `{"type":"csp-violation","body":{...}}`) are accepted.
 *
 * Each parsed report becomes one JSON-per-line entry in
 * `/home/pat/DB/csp.log` (the DB dir is the only www-data-writable path
 * inside the existing PHP open_basedir). Rotated weekly by
 * /etc/logrotate.d/kayak-csp; harvested into the release directory by
 * ../../logs/syncit.
 */

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    http_response_code(405);
    header('Allow: POST');
    exit;
}

$body = file_get_contents('php://input', false, null, 0, 32768);
if (!is_string($body) || $body === '') {
    http_response_code(204);
    exit;
}

$decoded = json_decode($body, true);
if (!is_array($decoded)) {
    http_response_code(204);
    exit;
}

$reports = [];
if (isset($decoded['csp-report']) && is_array($decoded['csp-report'])) {
    // CSP Level 1
    $reports[] = $decoded['csp-report'];
} elseif (array_is_list($decoded)) {
    // Reporting API v2 — array of reports
    foreach ($decoded as $entry) {
        if (!is_array($entry)) continue;
        $inner = $entry['body'] ?? $entry;
        if (is_array($inner)) $reports[] = $inner;
    }
}

if (!$reports) {
    http_response_code(204);
    exit;
}

$lines = [];
foreach ($reports as $r) {
    $lines[] = json_encode([
        'ts'           => date('c'),
        'ip'           => $_SERVER['REMOTE_ADDR']      ?? '-',
        'ua'           => $_SERVER['HTTP_USER_AGENT']  ?? '-',
        'document_uri' => $r['document-uri']        ?? $r['documentURL']      ?? null,
        'referrer'     => $r['referrer']            ?? null,
        'violated'     => $r['violated-directive']  ?? $r['effectiveDirective'] ?? null,
        'effective'    => $r['effective-directive'] ?? null,
        'blocked'      => $r['blocked-uri']         ?? $r['blockedURL']       ?? null,
        'source_file'  => $r['source-file']         ?? null,
        'line'         => $r['line-number']         ?? $r['lineNumber']       ?? null,
    ], JSON_UNESCAPED_SLASHES);
}

@file_put_contents(
    '/home/pat/DB/csp.log',
    implode("\n", $lines) . "\n",
    FILE_APPEND | LOCK_EX
);

http_response_code(204);
