<?php
declare(strict_types=1);
/**
 * HTML rendering helpers.
 *
 * Escaping convention: bare `htmlspecialchars($s)` everywhere. PHP 8.1+
 * defaults to ENT_QUOTES | ENT_SUBSTITUTE | ENT_HTML401 with charset
 * UTF-8, which is correct for both body text and double-quoted HTML
 * attributes. The redundant `, ENT_QUOTES, 'UTF-8'` form was scrubbed
 * to keep call sites uniform. For ISO-8601 timestamps that go in <time>
 * tags, prefer `gmdate('Y-m-d\TH:i:s\Z', ...)` over `date(...)` even
 * when the server timezone is UTC -- explicit beats implicit.
 */

/**
 * Escape $text for HTML output, replacing any http(s):// URLs with clickable
 * anchor tags. URLs and surrounding text are each escaped separately so that
 * ampersands in query strings survive intact.
 */
function autolink_urls(string $text): string {
    $trailing = '.,;:!?)]}>';
    $result = '';
    $offset = 0;
    if (preg_match_all('#https?://[^\s<>"\']+#', $text, $matches, PREG_OFFSET_CAPTURE) !== false) {
        foreach ($matches[0] as $m) {
            $url = rtrim($m[0], $trailing);
            if ($url === '') continue;
            $pos = $m[1];
            $result .= htmlspecialchars(substr($text, $offset, $pos - $offset));
            $esc = htmlspecialchars($url);
            $result .= '<a href="' . $esc . '" target="_blank" rel="noopener">' . $esc . '</a>';
            $offset = $pos + strlen($url);
        }
    }
    $result .= htmlspecialchars(substr($text, $offset));
    return $result;
}
