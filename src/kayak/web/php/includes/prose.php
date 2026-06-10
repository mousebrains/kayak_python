<?php

declare(strict_types=1);

/**
 * Dataset-rendered prose fragments (S3c).
 *
 * `levels build` renders DATASET_DIR/site/<page>.md → <docroot>/prose/<page>.html
 * (sanitized HTML via the S2 markdown sanitizer). The prose pages call
 * prose_fragment() and echo the fragment when present, falling back to their
 * built-in body otherwise. The fragment is resolved via the request-time
 * DOCUMENT_ROOT, not __DIR__/.. — under the dev public_html/includes symlink
 * __DIR__ points at the source tree, which has no rendered fragment (same reason
 * css_head_block in header.php uses DOCUMENT_ROOT).
 */
function prose_fragment(string $page): ?string
{
    $doc_root = $_SERVER['DOCUMENT_ROOT'] ?? (__DIR__ . '/..');
    $path = (is_string($doc_root) ? $doc_root : (__DIR__ . '/..')) . '/prose/' . $page . '.html';
    if (!is_readable($path)) {
        return null;
    }
    $html = file_get_contents($path);
    return $html === false ? null : $html;
}
