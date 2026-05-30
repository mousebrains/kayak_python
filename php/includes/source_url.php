<?php
declare(strict_types=1);
/**
 * Source-URL helper for submission forms (contact / comment / propose).
 *
 * The "source URL" is the page the user was on when they clicked the link
 * that led them to the form. Captured from HTTP_REFERER on the initial GET,
 * stashed into a hidden form input, and round-tripped through the POST so
 * the maintainer's notification email and the change_request row can
 * record what page triggered the submission.
 *
 * Both GET and POST go through sanitize_source_url() so a tampered hidden
 * input cannot splice email headers or leak cross-origin URLs.
 */

/**
 * Sanitize a candidate source URL. Returns '' when unusable.
 *
 *   - strips values > 2048 chars
 *   - rejects any CR/LF/TAB/NUL: CR/LF/NUL would splice the email header (the
 *     value is emailed unescaped in a plain-text body); an embedded TAB (like
 *     CR/LF) would otherwise let a javascript:/data: URI slip the scheme check
 *     below — browsers strip TAB/LF/CR per the WHATWG URL spec
 *   - rejects any non-http(s) scheme (javascript:, data:, …) so a tampered
 *     hidden field can't store a clickable XSS URI for the maintainer review UI
 *   - accepts relative paths ("/description.php?id=42")
 *   - for absolute URLs, host must match the current request's host
 *     (same-origin), so we never record where an external referrer came from
 */
function sanitize_source_url(string $raw): string {
    $raw = trim($raw);
    if ($raw === '') return '';
    if (strlen($raw) > 2048) return '';
    if (preg_match('/[\r\n\t\0]/', $raw) === 1) return '';
    $parts = @parse_url($raw);
    if ($parts === false) return '';
    // Reject non-http(s) schemes (javascript:, data:, vbscript:, …). parse_url
    // gives these no host, so they would otherwise slip through the relative-path
    // branch below. parse_url does not case-fold, so lowercase before comparing.
    $scheme = strtolower($parts['scheme'] ?? '');
    if ($scheme !== '' && $scheme !== 'http' && $scheme !== 'https') return '';
    $host = $parts['host'] ?? '';
    if ($host === '') return $raw;  // relative path (no scheme) — always OK
    // HTTP_HOST carries "host:port" when the port is non-default; parse_url
    // returns them separately. Strip the :port before comparing.
    $self_host = (string)($_SERVER['HTTP_HOST'] ?? '');
    $self_host = preg_replace('/:\d+$/', '', $self_host);
    if (strcasecmp($host, (string)$self_host) !== 0) return '';
    return $raw;
}

/**
 * Pull a source URL for the initial GET render of a form page.
 * $self_path is the path of the form itself (e.g. "/contact.php") — a
 * referrer matching that path is dropped so a reload-from-form doesn't
 * overwrite the original source.
 */
function source_url_from_referrer(string $self_path): string {
    $ref = sanitize_source_url((string)($_SERVER['HTTP_REFERER'] ?? ''));
    if ($ref === '') return '';
    $ref_parts = @parse_url($ref);
    $path = $ref_parts['path'] ?? '';
    if ($path === $self_path) return '';
    return $ref;
}
