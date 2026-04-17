<?php
declare(strict_types=1);
/**
 * HTML rendering helpers.
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
    if (preg_match_all('#https?://[^\s<>"\']+#', $text, $matches, PREG_OFFSET_CAPTURE)) {
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
