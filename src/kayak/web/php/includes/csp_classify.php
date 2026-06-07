<?php

declare(strict_types=1);

/**
 * Classify one CSP-report log line into a coarse "likely source" bucket so the
 * _internal dashboard can hint at the cause at a glance. Operates on the
 * *normalized* shape csp-report.php writes (keys: source_file, blocked,
 * document_uri, …); the camelCase Reporting-API names are kept as fallbacks.
 * Returns one of: "Firefox extension", "Chrome/Edge extension",
 * "Safari extension", "Ad blocker", "Injected (proxy/extension)",
 * "Same-origin (our code)", "Browser internal[ (blocked)]", "Other".
 *
 * Heuristics, in priority order:
 *   - source_file / blocked naming an extension scheme → that extension bucket.
 *   - ad-blocker asset names (adblock, uBlock, …) → "Ad blocker".
 *   - inline / eval / wasm-eval (or blank) blocked WITH source_file equal to
 *     the document URL → "Injected (proxy/extension)". These violations have
 *     no real script file, so the browser reports the *page* URL as
 *     source-file; a value equal to the document is an injected inline script
 *     — a transcoding proxy (e.g. Google Web Light, whose google-proxy-*.
 *     google.com fleet produced the burst that motivated this bucket), a
 *     translator, an extension, or AV — NOT code we authored. Our pages ship
 *     zero inline <script> and our JS never evals, so a same-origin inline /
 *     eval violation is never genuinely ours.
 *   - source_file pointing at a *real* same-origin asset (a path past the bare
 *     page, e.g. /static/foo.js:42) → "Same-origin (our code)".
 *   - empty source_file → "Browser internal" (Chrome incognito eval/wasm
 *     probes; the blocked value names what was blocked).
 *
 * @param array<string, mixed> $data Decoded CSP report payload (one log line).
 */
function csp_classify(array $data): string
{
    // Read a (possibly-absent, possibly-non-string) field as a lowercased
    // string — narrowing avoids casting the array's `mixed` values.
    $str = static fn (mixed $v): string => is_string($v) ? strtolower($v) : '';

    $src     = $str($data['source_file'] ?? $data['sourceFile'] ?? null);
    $blocked = $str($data['blocked'] ?? $data['blocked_uri'] ?? $data['blockedURL'] ?? null);
    $doc     = $str($data['document_uri'] ?? $data['documentURL'] ?? null);
    $hay     = $src . ' ' . $blocked;

    if (str_contains($hay, 'moz-extension'))    { return 'Firefox extension'; }
    if (str_contains($hay, 'chrome-extension')) { return 'Chrome/Edge extension'; }
    if (str_contains($hay, 'safari-extension')) { return 'Safari extension'; }
    if (str_contains($hay, 'adblock')
        || str_contains($hay, 'ublock')
        || str_contains($hay, 'ghostery')
        || str_contains($hay, 'privacy-badger')) {
        return 'Ad blocker';
    }

    // Inline / eval / wasm-eval has no real script file, so the browser sets
    // source-file to the *document* URL. A source_file equal to the document
    // (query/fragment aside) is therefore an injected inline script — a proxy,
    // translator, extension, or AV — not authored code. Bucket it separately
    // so it stops masquerading as "Same-origin (our code)".
    $strip = static fn (string $u): string => explode('#', explode('?', $u, 2)[0], 2)[0];
    $no_real_file = $blocked === '' || in_array($blocked, ['inline', 'eval', 'wasm-eval'], true);
    if ($no_real_file && $src !== '' && $doc !== '' && $strip($src) === $strip($doc)) {
        return 'Injected (proxy/extension)';
    }

    // A source_file pointing at a real same-origin asset (not the bare page) is
    // genuinely our code — worth surfacing distinctly.
    if ($src !== '' && (str_starts_with($src, 'https://levels.')
                        || str_starts_with($src, 'http://levels.'))) {
        return 'Same-origin (our code)';
    }
    if ($src === '') {
        // No source path — usually a browser-internal eval/wasm probe (Chrome
        // incognito + extensions are big offenders). The blocked value names
        // what was blocked: wasm-eval, eval, data, inline, blob, …
        if ($blocked !== '') {
            return 'Browser internal (' . $blocked . ')';
        }
        return 'Browser internal';
    }
    return 'Other';
}
